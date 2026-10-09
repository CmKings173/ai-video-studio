"""Prepare immutable generation snapshots before external execution begins."""

from __future__ import annotations

import copy
import math
import re
import secrets
from dataclasses import fields
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    DirectorRunMember,
    GenerationAsset,
    Product,
    Project,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
    new_id,
)
from apps.api.app.providers.minimax_h3_director.graph_identity import is_director_graph
from apps.api.app.providers.minimax_h3_director.plan_builder import DirectorPlanBuilder
from apps.api.app.schemas.api import (
    DirectorAudioPolicy,
    DirectorFaceRefine,
    DirectorMotionContext,
    DirectorRefine,
    DirectorTimelineSegment,
    GenerationRequest,
    SceneGenerationConfig,
)
from apps.api.app.services.continuity_groups import requires_native_execution_group
from apps.api.app.services.domain_guards import require_active_brand
from apps.api.app.services.generation_dependency_invalidation import (
    lock_pending_dependency_bindings,
)
from apps.api.app.services.generation_freshness import (
    capture_generation_freshness,
    lock_generation_dependencies,
)
from apps.api.app.services.generation_intent import (
    GenerationAssetBinding,
    GenerationCanvas,
    GenerationIntent,
    GenerationTimelineSegment,
    PromptProvenance,
)
from apps.api.app.services.h3_validator import (
    H3Profile,
    H3Request,
    H3ValidationError,
    H3Validator,
)
from apps.api.app.services.prompt_engine import PromptEngine
from apps.api.app.services.workflow_contracts import (
    approved_record,
    frozen_ratio,
    profile_hash,
    require_contract,
    require_frozen,
    resolve_canvas,
    resolve_frames,
)
from apps.api.app.services.workflow_registry import (
    ApprovedWorkflow,
    _stable_hash,
)
from apps.api.app.services.workflow_router import (
    RoutingInput,
    WorkflowRoutingError,
    director_task_for_mode,
    require_director_execution,
    select_mode,
)

JS_SAFE_SEED_BOUND = 9_007_199_254_740_992


def _inherit_director_timeline(
    frozen_timeline: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[list[DirectorTimelineSegment], tuple[GenerationTimelineSegment, ...]]:
    """Project frozen timeline metadata into the public request while retaining bindings."""
    inherited = tuple(GenerationTimelineSegment.model_validate(item) for item in frozen_timeline)
    public_fields = DirectorTimelineSegment.model_fields
    request_timeline = [
        DirectorTimelineSegment.model_validate(
            {
                key: value
                for key, value in segment.model_dump(mode="json").items()
                if key in public_fields
            }
        )
        for segment in inherited
    ]
    return request_timeline, inherited


class GenerationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.prompt_engine = PromptEngine(settings)

    async def _require_direct_scene(self, session, scene, defer_aggregate_qualification):
        if defer_aggregate_qualification:
            return
        scenes = list(
            (await session.scalars(select(Scene).where(Scene.video_id == scene.video_id))).all()
        )
        if requires_native_execution_group(scene, scenes):
            raise AppError(
                "DIRECTOR_AGGREGATE_REQUIRED",
                "Every member of a native continuity chain must use Generate All",
                422,
            )

    @staticmethod
    def _require_motion_scope(request, defer_aggregate_qualification):
        if (
            request.motion_context
            and request.motion_context.enabled
            and not defer_aggregate_qualification
        ):
            raise AppError(
                "DIRECTOR_AGGREGATE_REQUIRED",
                "Motion Context must use chain-aware Generate All",
                422,
            )

    @staticmethod
    def _ids(values: list[Any]) -> list[str]:
        return [str(value) for value in values]

    async def _load_assets(
        self, session: AsyncSession, request: GenerationRequest, video: Video
    ) -> tuple[list[tuple[str, int, Asset]], list[float], list[float]]:
        requested: list[tuple[str, int, str, str]] = []
        if request.first_frame_asset_id:
            requested.append(("FIRST_FRAME", 0, str(request.first_frame_asset_id), "IMAGE"))
        if request.last_frame_asset_id:
            requested.append(("LAST_FRAME", 0, str(request.last_frame_asset_id), "IMAGE"))
        if request.source_video_asset_id:
            requested.append(("SOURCE_VIDEO", 0, str(request.source_video_asset_id), "VIDEO"))
        for role, values, kind in (
            ("REFERENCE_IMAGE", request.reference_image_asset_ids, "IMAGE"),
            ("REFERENCE_AUDIO", request.reference_audio_asset_ids, "AUDIO"),
            ("REFERENCE_VIDEO", request.reference_video_asset_ids, "VIDEO"),
        ):
            requested.extend(
                (role, index, str(asset_id), kind) for index, asset_id in enumerate(values)
            )
        ids = [item[2] for item in requested]
        if len(ids) != len(set(ids)):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "The same asset cannot occupy multiple generation inputs",
                422,
            )
        assets = {
            asset.id: asset
            for asset in (
                await session.scalars(select(Asset).where(Asset.id.in_(ids)).with_for_update())
            ).all()
        }
        resolved: list[tuple[str, int, Asset]] = []
        audio_durations: list[float] = []
        video_durations: list[float] = []
        for role, order, asset_id, expected_kind in requested:
            asset = assets.get(asset_id)
            if asset is None or asset.deleted_at is not None:
                raise AppError("ASSET_NOT_FOUND", f"Asset {asset_id} was not found", 404)
            if (
                asset.status != "READY"
                or not asset.checksum
                or not re.fullmatch(r"[a-f0-9]{64}", asset.checksum)
            ):
                raise AppError("ASSET_NOT_READY", f"Asset {asset_id} is not ready", 409)
            if not (
                asset.project_id == video.project_id
                or (video.product_id is not None and asset.product_id == video.product_id)
            ):
                raise AppError(
                    "ASSET_SCOPE_INVALID", "Asset is outside this video project/product", 422
                )
            if asset.kind != expected_kind:
                raise AppError(
                    "GENERATION_INPUT_INVALID",
                    f"{role} requires a {expected_kind.lower()} asset",
                    422,
                    {"asset_id": asset_id, "actual_kind": asset.kind},
                )
            metadata = asset.media_metadata or {}
            if not isinstance(metadata, dict):
                raise AppError("ASSET_METADATA_MISSING", "Media metadata must be an object", 409)
            if expected_kind in {"IMAGE", "VIDEO"} and (not asset.width or not asset.height):
                raise AppError(
                    "ASSET_METADATA_MISSING", "Visual asset dimensions are required", 409
                )
            if role in {"REFERENCE_AUDIO", "REFERENCE_VIDEO", "SOURCE_VIDEO"}:
                if asset.duration_seconds is None:
                    raise AppError(
                        "ASSET_METADATA_MISSING",
                        f"Media asset {asset_id} has no duration metadata",
                        409,
                    )
                if not metadata.get("codec"):
                    raise AppError(
                        "ASSET_METADATA_MISSING", "Reference codec metadata is required", 409
                    )
                if role in {"REFERENCE_VIDEO", "SOURCE_VIDEO"}:
                    fps = metadata.get("fps")
                    duration = metadata.get("video_duration_seconds")
                    if type(fps) not in {int, float} or not math.isfinite(fps) or fps <= 0:
                        raise AppError(
                            "ASSET_METADATA_MISSING", "Video FPS metadata is required", 409
                        )
                    if role == "REFERENCE_VIDEO" and fps != 24:
                        raise AppError(
                            "GENERATION_INPUT_INVALID",
                            "Reference video must be normalized to 24 FPS",
                            422,
                        )
                    if type(duration) not in {int, float} or not math.isfinite(duration):
                        raise AppError(
                            "ASSET_METADATA_MISSING",
                            "Reference video stream duration is required",
                            409,
                        )
                    if role == "REFERENCE_VIDEO":
                        video_durations.append(duration)
                else:
                    duration = metadata.get("audio_duration_seconds")
                    if type(duration) not in {int, float} or not math.isfinite(duration):
                        raise AppError(
                            "ASSET_METADATA_MISSING",
                            "Reference audio stream duration is required",
                            409,
                        )
                    audio_durations.append(duration)
            resolved.append((role, order, asset))
        return resolved, audio_durations, video_durations

    async def _workflow(
        self,
        session: AsyncSession,
        mode: str,
        workflow_id: Any | None,
        quality: str = "STANDARD",
        ratio: str | None = None,
        *,
        allow_aggregate: bool = False,
    ) -> tuple[WorkflowRecord, ApprovedWorkflow]:
        execution_scope = "aggregate" if allow_aggregate else "single_scene"
        canonical_mode = director_task_for_mode(mode)
        compatible_modes = [canonical_mode]
        if mode != canonical_mode:
            compatible_modes.append(mode)
        if workflow_id:
            record = await session.get(WorkflowRecord, str(workflow_id))
            if record is None:
                raise AppError("WORKFLOW_NOT_FOUND", "Workflow was not found", 404)
            require_director_execution(record)
            if not record.enabled:
                raise AppError("WORKFLOW_NOT_APPROVED", "Workflow is not approved", 409)
            if record.mode not in compatible_modes:
                raise AppError(
                    "WORKFLOW_MODE_CONFLICT",
                    "Workflow mode does not match generation inputs",
                    422,
                )
        else:
            records = list(
                (
                    await session.scalars(
                        select(WorkflowRecord)
                        .where(
                            WorkflowRecord.mode.in_(compatible_modes),
                            WorkflowRecord.enabled.is_(True),
                            WorkflowRecord.quality_profile == quality,
                            WorkflowRecord.execution_scope == execution_scope,
                        )
                        .order_by(
                            WorkflowRecord.approved_at.desc(), WorkflowRecord.created_at.desc()
                        )
                    )
                ).all()
            )
            records = [
                value
                for value in records
                if is_director_graph(value.workflow) and not value.profile.get("retired")
            ]
            record = records[0] if records else None
            if record is None:
                raise AppError(
                    "WORKFLOW_NOT_CONFIGURED",
                    f"No approved workflow is configured for {mode}",
                    409,
                )
        if record.execution_scope != execution_scope:
            raise AppError(
                "WORKFLOW_EXECUTION_SCOPE_CONFLICT",
                f"Workflow requires {record.execution_scope}, requested {execution_scope}",
                422,
            )
        require_director_execution(record)
        approved = approved_record(record)
        if (
            approved.workflow_hash != record.workflow_hash
            or approved.slot_map_hash != record.slot_map_hash
        ):
            raise AppError(
                "WORKFLOW_INTEGRITY_FAILED",
                "Approved workflow content no longer matches its hashes",
                409,
            )
        require_contract(record, approved, quality, ratio)
        return record, approved

    @staticmethod
    def _profile(record: WorkflowRecord) -> H3Profile:
        allowed = {item.name for item in fields(H3Profile)}
        values = {key: value for key, value in record.profile.items() if key in allowed}
        try:
            return H3Profile(**values)
        except (TypeError, ValueError) as exc:
            raise AppError("WORKFLOW_PROFILE_INVALID", str(exc), 409) from exc

    @staticmethod
    def _is_director(approved: ApprovedWorkflow) -> bool:
        return is_director_graph(approved.workflow)

    @staticmethod
    def _asset_manifest(assets: list[tuple[str, int, Asset]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for role, order, asset in assets:
            extension = Path(asset.filename).suffix.lower()
            result.append(
                {
                    "id": asset.id,
                    "object_key": asset.object_key,
                    "checksum": asset.checksum,
                    "role": role,
                    "order_index": order,
                    "filename": f"{asset.id}{extension}",
                    "content_type": asset.content_type,
                    "size_bytes": asset.size_bytes,
                    "media_metadata": copy.deepcopy(asset.media_metadata or {}),
                }
            )
        return result

    @staticmethod
    def _intent_assets(manifest: list[dict[str, Any]]) -> tuple[GenerationAssetBinding, ...]:
        return tuple(
            GenerationAssetBinding(
                id=item["id"],
                role=item["role"],
                order_index=item["order_index"],
                checksum=item["checksum"],
                filename=item["filename"],
                content_type=item["content_type"],
                object_key=item["object_key"],
                size_bytes=item.get("size_bytes", 0),
                media_metadata=copy.deepcopy(item.get("media_metadata") or {}),
            )
            for item in manifest
        )

    @staticmethod
    def _director_settings(request: GenerationRequest) -> tuple[dict, dict, dict, dict]:
        return (
            (request.motion_context or DirectorMotionContext()).model_dump(mode="json"),
            (request.refine or DirectorRefine()).model_dump(mode="json"),
            (request.face_refine or DirectorFaceRefine()).model_dump(mode="json"),
            (request.audio_policy or DirectorAudioPolicy()).model_dump(mode="json"),
        )

    def _build_director_intent(
        self,
        *,
        request: GenerationRequest,
        mode: str,
        scene: Scene,
        quality: str,
        ratio: str,
        width: int,
        height: int,
        frames: int,
        fps: int,
        steps: int,
        seed: int,
        prompt: str,
        negative_prompt: str,
        assets: list[dict[str, Any]],
        accepted_prompt: bool,
        inherited_from: str | None = None,
        inherited_timeline: tuple[GenerationTimelineSegment, ...] | None = None,
        default_cfg: float = 1.0,
        requested_duration_seconds: float | None = None,
    ) -> GenerationIntent:
        timeline = inherited_timeline
        if timeline is None:
            timeline = tuple(
                GenerationTimelineSegment(
                    scene_id=str(item.scene_id) if item.scene_id else None,
                    prompt=item.prompt,
                    start_frame=item.start_frame,
                    frame_count=item.frame_count,
                    continuity_from_previous=item.continuity_from_previous,
                )
                for item in (request.timeline or [])
            )
        if not timeline:
            timeline = (
                GenerationTimelineSegment(
                    scene_id=scene.id,
                    prompt=prompt,
                    start_frame=0,
                    frame_count=frames,
                    continuity_from_previous=False,
                ),
            )
        cursor = 0
        for item in timeline:
            if item.scene_id not in (None, scene.id):
                raise AppError(
                    "DIRECTOR_AGGREGATE_REQUIRED",
                    "Cross-scene timelines require an aggregate Director run",
                    422,
                )
            if item.start_frame != cursor:
                raise AppError(
                    "GENERATION_INPUT_INVALID",
                    "Director timeline segments must be ordered and contiguous",
                    422,
                )
            cursor += item.frame_count
        if cursor != frames:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Director timeline must cover the qualified generation frame count exactly",
                422,
            )
        motion, refine, face, audio = self._director_settings(request)
        return GenerationIntent(
            requested_mode=mode,
            provider_task=director_task_for_mode(mode),
            prompt=prompt,
            negative_prompt=negative_prompt,
            prompt_provenance=PromptProvenance(
                accepted=accepted_prompt,
                source_scene_revision=request.source_scene_revision,
                source_video_revision=request.source_video_revision,
                inherited_from=inherited_from,
            ),
            canvas=GenerationCanvas(width=width, height=height, aspect_ratio=ratio),
            quality_profile=quality,
            seed=seed,
            seed_policy=request.seed_policy or ("FIXED" if request.seed is not None else "RANDOM"),
            steps=steps,
            cfg=request.cfg if request.cfg is not None else default_cfg,
            fps=fps,
            frames=frames,
            requested_duration_seconds=(
                scene.duration_seconds
                if requested_duration_seconds is None
                else requested_duration_seconds
            ),
            resolved_duration_seconds=frames / fps,
            assets=self._intent_assets(assets),
            timeline=timeline,
            motion_context=motion,
            refine=refine,
            face_refine=face,
            audio_policy=audio,
        )

    @staticmethod
    def effective_request(scene: Scene, request: GenerationRequest) -> GenerationRequest:
        config = SceneGenerationConfig.model_validate(scene.generation_config or {})
        values = config.model_dump(mode="json", exclude_unset=True)
        values.update(request.model_dump(mode="json", exclude_unset=True))
        if values.get("seed_policy") == "RANDOM" and "seed" not in request.model_fields_set:
            values.pop("seed", None)
        return GenerationRequest.model_validate(values)

    @staticmethod
    def finish_prompt(
        prompt: str, negative: str, approved: ApprovedWorkflow, assets: list[tuple[str, int, Asset]]
    ) -> str:
        if negative and "NEGATIVE_PROMPT" not in approved.slots:
            prompt += f"\nAvoid / Constraints: {negative}"
        tags = []
        for role, label in (
            ("REFERENCE_IMAGE", "Picture"),
            ("REFERENCE_VIDEO", "Video"),
            ("REFERENCE_AUDIO", "Audio"),
        ):
            for index, _ in enumerate([item for item in assets if item[0] == role], 1):
                tags.append(f"<{label} {index}>")
        if tags:
            prompt = f"References: {', '.join(tags)}\n{prompt}"
        return prompt

    async def _from_parent(
        self,
        session,
        scene,
        video,
        request,
        user_id,
        request_id,
        *,
        defer_aggregate_qualification: bool = False,
    ):
        parent = await session.get(SceneGeneration, str(request.parent_generation_id))
        if parent is None or parent.scene_id != scene.id:
            raise AppError("PARENT_GENERATION_INVALID", "Parent must belong to the same scene", 422)
        freshness = (parent.input_snapshot or {}).get("generation_freshness") or {}
        if freshness.get("execution_group") or await session.scalar(
            select(DirectorRunMember.id).where(DirectorRunMember.scene_generation_id == parent.id)
        ):
            raise AppError(
                "DIRECTOR_DERIVATIVE_REQUIRES_CHAIN",
                "Regenerate Director execution group members through chain-aware Generate All",
                422,
            )
        if parent.status != "COMPLETED":
            raise AppError("PARENT_GENERATION_NOT_READY", "Parent must be completed", 409)
        await self._require_direct_scene(session, scene, defer_aggregate_qualification)
        frozen = copy.deepcopy(parent.input_snapshot)
        parent_record = await session.get(WorkflowRecord, parent.workflow_id)
        if parent_record is None or not parent_record.enabled:
            raise AppError("WORKFLOW_NOT_APPROVED", "Parent workflow is no longer enabled", 409)
        require_director_execution(parent_record)
        approved = require_frozen(frozen, parent_record)
        values = {
            "mode": parent.mode,
            "workflow_id": parent.workflow_id,
            "quality_profile": frozen.get(
                "requested_quality_profile", parent_record.quality_profile
            ),
            "aspect_ratio": frozen_ratio(frozen, parent_record, approved),
            "seed": frozen.get("seed"),
            "seed_policy": frozen.get("seed_policy", "RANDOM"),
        }
        inherited_timeline = None
        for role, field in (
            ("FIRST_FRAME", "first_frame_asset_id"),
            ("LAST_FRAME", "last_frame_asset_id"),
            ("SOURCE_VIDEO", "source_video_asset_id"),
            ("REFERENCE_IMAGE", "reference_image_asset_ids"),
            ("REFERENCE_VIDEO", "reference_video_asset_ids"),
            ("REFERENCE_AUDIO", "reference_audio_asset_ids"),
        ):
            ids = [
                item["id"]
                for item in sorted(frozen.get("assets", []), key=lambda v: v["order_index"])
                if item["role"] == role
            ]
            values[field] = ids if field.endswith("ids") else ids[0] if ids else None
        parent_intent = frozen.get("generation_intent") or {}
        request_timeline, inherited_timeline = _inherit_director_timeline(
            parent_intent.get("timeline") or ()
        )
        canvas = parent_intent.get("canvas") or {}
        values.update(
            width=canvas.get("width", frozen.get("resolved_width")),
            height=canvas.get("height", frozen.get("resolved_height")),
            cfg=parent_intent.get("cfg", frozen.get("cfg")),
            fps=parent_intent.get("fps", frozen.get("fps")),
            frames=parent_intent.get("frames", frozen.get("frames")),
            motion_context=parent_intent.get("motion_context"),
            refine=parent_intent.get("refine"),
            face_refine=parent_intent.get("face_refine"),
            audio_policy=parent_intent.get("audio_policy"),
            timeline=request_timeline,
        )
        overrides = request.model_dump(mode="json", exclude_unset=True)
        if ("width" in overrides) != ("height" in overrides):
            raise AppError(
                "GENERATION_INPUT_INVALID", "width and height must be supplied together", 422
            )
        values.update(overrides)
        if "timeline" in overrides:
            inherited_timeline = None
        input_fields = {
            "first_frame_asset_id",
            "last_frame_asset_id",
            "reference_image_asset_ids",
            "reference_video_asset_ids",
            "reference_audio_asset_ids",
            "source_video_asset_id",
        }
        if input_fields & overrides.keys() and "mode" not in overrides:
            values["mode"] = "AUTO"
        if (
            {"mode", "quality_profile"} | input_fields
        ) & overrides.keys() and "workflow_id" not in overrides:
            values["workflow_id"] = None
        if (
            request.operation == "REGENERATE"
            and "seed" not in overrides
            and values["seed_policy"] != "FIXED"
        ):
            values["seed"] = None
        if overrides.get("seed_policy") == "RANDOM" and "seed" not in overrides:
            values["seed"] = None
        inherited = GenerationRequest.model_validate(values)
        self._require_motion_scope(inherited, defer_aggregate_qualification)
        if (inherited.width is None) != (inherited.height is None):
            raise AppError(
                "GENERATION_INPUT_INVALID", "width and height must be supplied together", 422
            )
        if inherited.seed_policy == "FIXED" and inherited.seed is None:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "FIXED seed policy requires a frozen or explicit seed",
                422,
            )
        routing = RoutingInput(
            requested_mode=inherited.mode,
            first_frame_asset_id=str(inherited.first_frame_asset_id)
            if inherited.first_frame_asset_id
            else None,
            last_frame_asset_id=str(inherited.last_frame_asset_id)
            if inherited.last_frame_asset_id
            else None,
            reference_image_asset_ids=self._ids(inherited.reference_image_asset_ids),
            reference_video_asset_ids=self._ids(inherited.reference_video_asset_ids),
            reference_audio_asset_ids=self._ids(inherited.reference_audio_asset_ids),
            source_video_asset_id=(
                str(inherited.source_video_asset_id) if inherited.source_video_asset_id else None
            ),
        )
        try:
            mode = select_mode(routing)
        except WorkflowRoutingError as exc:
            raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc
        # Explicit reference replacement can change AUTO routing, never silently discard frames.
        ratio, quality = inherited.aspect_ratio, inherited.quality_profile
        record, target = await self._workflow(
            session,
            mode,
            inherited.workflow_id,
            quality,
            ratio,
            allow_aggregate=defer_aggregate_qualification,
        )
        contract = require_contract(record, target, quality, ratio)
        assets, audio, videos = await self._load_assets(session, inherited, video)
        old_assets = {item["id"]: item for item in frozen.get("assets", [])}
        for _, _, asset in assets:
            if asset.id in old_assets and (
                asset.checksum != old_assets[asset.id]["checksum"]
                or asset.object_key != old_assets[asset.id]["object_key"]
            ):
                raise AppError("ASSET_INTEGRITY_FAILED", "Parent reference bytes changed", 409)
        changed = set(overrides) - {
            "parent_generation_id",
            "operation",
            "seed",
            "seed_policy",
            "source_scene_revision",
            "source_video_revision",
        }
        prompt = inherited.execution_prompt if "execution_prompt" in overrides else frozen["prompt"]
        if prompt is None:
            raise AppError("GENERATION_INPUT_INVALID", "Explicit prompt cannot be null", 422)
        width, height = resolve_canvas(contract.resolution, target, ratio)
        if inherited.width is not None and (inherited.width, inherited.height) != (
            width,
            height,
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw canvas contradicts qualified profile", 422
            )
        if inherited.steps is not None and inherited.steps != contract.steps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw steps contradict qualified profile", 422
            )
        if inherited.fps is not None and inherited.fps != contract.fps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested FPS contradicts qualified profile", 422
            )
        resolved_frames = resolve_frames(contract, target, frozen["duration_seconds"])
        if inherited.frames is not None and inherited.frames != resolved_frames:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Requested frame count contradicts qualified profile",
                422,
            )
        profile_cfg = float(record.profile.get("cfg", 1.0))
        if inherited.cfg is not None and inherited.cfg != profile_cfg:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested CFG contradicts qualified profile", 422
            )
        try:
            H3Validator(self._profile(record)).validate(
                H3Request(
                    mode=mode,
                    width=width,
                    height=height,
                    duration_seconds=frozen["duration_seconds"],
                    first_frame_asset_id=routing.first_frame_asset_id,
                    last_frame_asset_id=routing.last_frame_asset_id,
                    reference_image_asset_ids=routing.reference_image_asset_ids,
                    reference_video_asset_ids=routing.reference_video_asset_ids,
                    reference_audio_asset_ids=routing.reference_audio_asset_ids,
                    source_video_asset_id=routing.source_video_asset_id,
                    reference_audio_durations=audio,
                    reference_video_durations=videos,
                )
            )
        except H3ValidationError as exc:
            raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc
        graph = copy.deepcopy(record.workflow)
        snapshot = {
            **frozen,
            "schema_version": 3,
            "base_workflow": copy.deepcopy(record.workflow),
            "mode": mode,
            "slots": record.slots,
            "required_slots": record.required_slots,
            "workflow_id": record.id,
            "workflow_code": record.code,
            "workflow_version": record.version,
            "workflow_hash": record.workflow_hash,
            "slot_map_hash": record.slot_map_hash,
            "runtime_profile": copy.deepcopy(record.profile),
            "profile_hash": profile_hash(record.profile),
            "requested_quality_profile": quality,
            "requested_aspect_ratio": ratio,
            "resolved_width": width,
            "resolved_height": height,
            "width": width,
            "height": height,
            "prompt": prompt,
            "steps": contract.steps,
            "frames": resolved_frames,
            "resolved_duration_seconds": resolved_frames / contract.fps,
            "cfg": profile_cfg,
            "fps": contract.fps,
            "prompt_provenance": {
                "accepted": "execution_prompt" in overrides,
                "source_scene_revision": request.source_scene_revision,
                "source_video_revision": request.source_video_revision,
                "inherited_from": parent.id,
            },
            "output_node": record.profile.get("output_node"),
        }
        generation_no = (
            await session.scalar(
                select(func.max(SceneGeneration.generation_no)).where(
                    SceneGeneration.scene_id == scene.id
                )
            )
            or 0
        ) + 1
        generation = SceneGeneration(
            id=new_id(),
            video_id=video.id,
            scene_id=scene.id,
            mode=mode,
            workflow_id=record.id,
            generation_no=generation_no,
            operation=request.operation,
            parent_generation_id=parent.id,
            status="CREATED",
            phase="PENDING",
            created_by=user_id,
            request_id=request_id,
        )
        seed = (
            inherited.seed if inherited.seed is not None else secrets.randbelow(JS_SAFE_SEED_BOUND)
        )
        output_prefix = f"studio/{video.id}/{scene.id}/{generation.id}"
        manifest = []
        for role, order, asset in assets:
            if asset.id in old_assets:
                manifest.append(
                    {**copy.deepcopy(old_assets[asset.id]), "role": role, "order_index": order}
                )
            else:
                manifest.append(
                    {
                        "id": asset.id,
                        "object_key": asset.object_key,
                        "checksum": asset.checksum,
                        "role": role,
                        "order_index": order,
                        "filename": f"{asset.id}{Path(asset.filename).suffix.lower()}",
                        "content_type": asset.content_type,
                        "media_metadata": asset.media_metadata,
                    }
                )
        resolved_width, resolved_height = resolve_canvas(contract.resolution, target, ratio)
        resolved_frames = resolve_frames(contract, target, frozen["duration_seconds"])
        profile_cfg = float(record.profile.get("cfg", 1.0))
        intent = self._build_director_intent(
            request=inherited,
            mode=mode,
            scene=scene,
            quality=quality,
            ratio=ratio,
            width=resolved_width,
            height=resolved_height,
            frames=resolved_frames,
            fps=contract.fps,
            steps=contract.steps,
            seed=seed,
            prompt=prompt,
            negative_prompt=frozen.get("negative_prompt", ""),
            assets=manifest,
            accepted_prompt="execution_prompt" in overrides,
            inherited_from=parent.id,
            inherited_timeline=inherited_timeline,
            default_cfg=profile_cfg,
            requested_duration_seconds=frozen["duration_seconds"],
        )
        director_spec = DirectorPlanBuilder().build(
            intent,
            output_prefix=output_prefix,
            workflow=record,
            profile_hash=profile_hash(record.profile),
            custom_node_versions=dict(record.profile.get("custom_node_versions") or {}),
            defer_aggregate_qualification=defer_aggregate_qualification,
        )
        snapshot.update(
            schema_version=3,
            generation_intent=intent.model_dump(mode="json"),
            director_execution=director_spec.model_dump(mode="json"),
            provider="minimax_h3_director",
            workflow=copy.deepcopy(record.workflow),
            resolved_width=resolved_width,
            resolved_height=resolved_height,
            width=resolved_width,
            height=resolved_height,
            frames=resolved_frames,
            resolved_duration_seconds=resolved_frames / contract.fps,
            steps=contract.steps,
            cfg=profile_cfg,
            fps=contract.fps,
        )
        snapshot.update(
            workflow=graph,
            seed=seed,
            seed_policy=inherited.seed_policy,
            assets=manifest,
            lineage={
                "parent_generation_id": parent.id,
                "parent_semantic_hash": frozen.get(
                    "semantic_hash", _stable_hash(parent.input_snapshot)
                ),
                "explicit_overrides": sorted(changed),
            },
            operation=request.operation,
        )
        # Derivatives execute frozen parent inputs, even after the editor changed.
        # Never certify those inputs against the current scene (or upgrade legacy
        # parents without a freshness identity).
        snapshot.pop("generation_freshness", None)
        if "generation_freshness" in parent.input_snapshot:
            snapshot["generation_freshness"] = copy.deepcopy(
                parent.input_snapshot["generation_freshness"]
            )
        snapshot.pop("semantic_hash", None)
        if snapshot.get("schema_version", 1) >= 2:
            snapshot["semantic_hash"] = _stable_hash(snapshot)
        generation.input_snapshot = snapshot
        # Qualification and snapshot construction must finish before any derivative write.
        session.add(generation)
        await session.flush()
        for role, order, asset in assets:
            session.add(
                GenerationAsset(
                    generation_id=generation.id, asset_id=asset.id, role=role, order_index=order
                )
            )
        if video.status not in {"ASSEMBLING", "DIRTY"}:
            video.status = "GENERATING"
        return generation

    async def create(
        self,
        session: AsyncSession,
        *,
        scene_id: str,
        request: GenerationRequest,
        user_id: str,
        request_id: str | None,
        defer_aggregate_qualification: bool = False,
        persist: bool = True,
    ) -> SceneGeneration:
        with session.no_autoflush:
            await lock_pending_dependency_bindings(session)
            video_id = await session.scalar(select(Scene.video_id).where(Scene.id == scene_id))
        if video_id is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        video = await lock_revisioned_row(session, Video, video_id)
        if video is None:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        await lock_generation_dependencies(session, video)
        scene = await lock_revisioned_row(session, Scene, scene_id, immutable_fields=("video_id",))
        if scene is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        if not scene.enabled:
            raise AppError("SCENE_DISABLED", "Disabled scenes cannot be generated", 409)
        if request.source_scene_revision is not None and (
            scene.revision != request.source_scene_revision
            or video.revision != request.source_video_revision
        ):
            raise AppError(
                "PROMPT_PREVIEW_STALE",
                "Scene or video changed after prompt preview",
                412,
                {
                    "scene_revision": scene.revision,
                    "video_revision": video.revision,
                },
            )
        project = await session.get(Project, video.project_id)
        if project is None or project.archived:
            raise AppError("PROJECT_NOT_ACTIVE", "Project is missing or archived", 409)
        if request.operation in {"VARIATION", "REGENERATE"}:
            if not persist:
                raise AppError(
                    "GENERATION_INPUT_INVALID", "Batch preparation requires ORIGINAL", 422
                )
            product = await session.get(Product, video.product_id) if video.product_id else None
            if product is not None and product.archived:
                raise AppError("PRODUCT_NOT_ACTIVE", "Product is archived", 409)
            await require_active_brand(
                session, video.brand_id or (product.brand_id if product else None)
            )
            return await self._from_parent(
                session,
                scene,
                video,
                request,
                user_id,
                request_id,
                defer_aggregate_qualification=defer_aggregate_qualification,
            )
        await self._require_direct_scene(session, scene, defer_aggregate_qualification)
        request = self.effective_request(scene, request)
        self._require_motion_scope(request, defer_aggregate_qualification)
        if not 4 <= scene.duration_seconds <= 15:
            raise AppError("SCENE_DURATION_INVALID", "Scene duration must be 4-15 seconds", 422)

        routing = RoutingInput(
            requested_mode=request.mode,
            first_frame_asset_id=(
                str(request.first_frame_asset_id) if request.first_frame_asset_id else None
            ),
            last_frame_asset_id=(
                str(request.last_frame_asset_id) if request.last_frame_asset_id else None
            ),
            reference_image_asset_ids=self._ids(request.reference_image_asset_ids),
            reference_audio_asset_ids=self._ids(request.reference_audio_asset_ids),
            reference_video_asset_ids=self._ids(request.reference_video_asset_ids),
            source_video_asset_id=(
                str(request.source_video_asset_id) if request.source_video_asset_id else None
            ),
        )
        try:
            mode = select_mode(routing)
        except WorkflowRoutingError as exc:
            raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc

        if bool(request.width) != bool(request.height):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "width and height must be supplied together",
                422,
            )
        ratio = request.aspect_ratio or video.aspect_ratio
        quality = request.quality_profile or "STANDARD"
        workflow_record, approved = await self._workflow(
            session,
            mode,
            request.workflow_id,
            quality,
            ratio,
            allow_aggregate=defer_aggregate_qualification,
        )
        contract = require_contract(workflow_record, approved, quality, ratio)
        width, height = resolve_canvas(contract.resolution, approved, ratio)
        if request.width is not None and (request.width, request.height) != (width, height):
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw canvas contradicts the qualified profile", 422
            )
        if request.steps is not None and request.steps != contract.steps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw steps contradict the qualified profile", 422
            )
        if request.fps is not None and request.fps != contract.fps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested FPS contradicts the qualified profile", 422
            )
        resolved_frames = resolve_frames(contract, approved, scene.duration_seconds)
        if request.frames is not None and request.frames != resolved_frames:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Requested frame count contradicts the qualified profile",
                422,
            )
        profile_cfg = float(workflow_record.profile.get("cfg", 1.0))
        if request.cfg is not None and request.cfg != profile_cfg:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested CFG contradicts the qualified profile", 422
            )
        assets, audio_durations, video_durations = await self._load_assets(session, request, video)
        try:
            validated = H3Validator(self._profile(workflow_record)).validate(
                H3Request(
                    mode=mode,
                    width=width,
                    height=height,
                    duration_seconds=scene.duration_seconds,
                    first_frame_asset_id=routing.first_frame_asset_id,
                    last_frame_asset_id=routing.last_frame_asset_id,
                    reference_image_asset_ids=routing.reference_image_asset_ids,
                    reference_audio_asset_ids=routing.reference_audio_asset_ids,
                    reference_video_asset_ids=routing.reference_video_asset_ids,
                    source_video_asset_id=routing.source_video_asset_id,
                    reference_audio_durations=audio_durations,
                    reference_video_durations=video_durations,
                )
            )
        except H3ValidationError as exc:
            raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc

        product = await session.get(Product, video.product_id) if video.product_id else None
        if product is not None and product.archived:
            raise AppError("PRODUCT_NOT_ACTIVE", "Product is archived", 409)
        brand_id = video.brand_id or (product.brand_id if product else None)
        brand = await require_active_brand(session, brand_id)
        prompt_result = await self.prompt_engine.compose(
            {
                "brief": video.brief,
                "scene": {
                    "prompt": scene.prompt,
                    "negative_prompt": scene.negative_prompt,
                    "spec": scene.spec,
                },
                "product": (
                    {
                        "name": product.name,
                        "description": product.description,
                        "context": product.context,
                    }
                    if product
                    else {}
                ),
                "brand": (
                    {
                        "name": brand.name,
                        "description": brand.description,
                        "context": brand.context,
                    }
                    if brand
                    else {}
                ),
                "music": video.config.get("music", ""),
            },
            None,
        )
        execution_prompt = (
            request.execution_prompt
            if request.execution_prompt is not None
            else self.finish_prompt(
                prompt_result.execution_prompt, scene.negative_prompt, approved, assets
            )
        )

        generation_no = (
            await session.scalar(
                select(func.max(SceneGeneration.generation_no)).where(
                    SceneGeneration.scene_id == scene.id
                )
            )
            or 0
        ) + 1
        generation = SceneGeneration(
            id=str(uuid4()),
            video_id=video.id,
            scene_id=scene.id,
            mode=mode,
            workflow_id=workflow_record.id,
            generation_no=generation_no,
            operation=request.operation,
            parent_generation_id=None,
            status="CREATED",
            phase="PENDING",
            request_id=request_id,
            created_by=user_id,
        )
        seed = request.seed if request.seed is not None else secrets.randbelow(JS_SAFE_SEED_BOUND)
        output_prefix = f"studio/{video.id}/{scene.id}/{generation.id}"
        asset_snapshot = self._asset_manifest(assets)
        intent = self._build_director_intent(
            request=request,
            mode=mode,
            scene=scene,
            quality=quality,
            ratio=ratio,
            width=validated.width,
            height=validated.height,
            frames=resolved_frames,
            fps=contract.fps,
            steps=contract.steps,
            seed=seed,
            prompt=execution_prompt,
            negative_prompt=scene.negative_prompt,
            assets=asset_snapshot,
            accepted_prompt=request.execution_prompt is not None,
            default_cfg=profile_cfg,
        )
        director_spec = DirectorPlanBuilder().build(
            intent,
            output_prefix=output_prefix,
            workflow=workflow_record,
            profile_hash=profile_hash(workflow_record.profile),
            custom_node_versions=dict(workflow_record.profile.get("custom_node_versions") or {}),
            defer_aggregate_qualification=defer_aggregate_qualification,
        )
        patched = copy.deepcopy(workflow_record.workflow)

        generation.input_snapshot = {
            "schema_version": 3,
            "base_workflow": copy.deepcopy(workflow_record.workflow),
            "mode": mode,
            "workflow": patched,
            "slots": workflow_record.slots,
            "required_slots": workflow_record.required_slots,
            "workflow_id": workflow_record.id,
            "workflow_code": workflow_record.code,
            "workflow_hash": workflow_record.workflow_hash,
            "slot_map_hash": workflow_record.slot_map_hash,
            "workflow_version": workflow_record.version,
            "runtime_profile": workflow_record.profile,
            "profile_hash": profile_hash(workflow_record.profile),
            "requested_aspect_ratio": ratio,
            "requested_quality_profile": quality,
            "resolved_width": width,
            "resolved_height": height,
            "seed_policy": request.seed_policy
            or ("FIXED" if request.seed is not None else "RANDOM"),
            "generation_config": scene.generation_config,
            "generation_freshness": await capture_generation_freshness(session, scene, video),
            "source_scope": {"project_id": video.project_id, "product_id": video.product_id},
            "prompt_provenance": {
                "accepted": request.execution_prompt is not None,
                "source_scene_revision": request.source_scene_revision,
                "source_video_revision": request.source_video_revision,
            },
            "scene_revision": scene.revision,
            "video_revision": video.revision,
            "raw_prompt": prompt_result.raw_prompt,
            "prompt": execution_prompt,
            "prompt_enhanced": prompt_result.enhanced,
            "prompt_warnings": list(prompt_result.warnings),
            "negative_prompt": scene.negative_prompt,
            "seed": seed,
            "width": validated.width,
            "height": validated.height,
            "frames": resolved_frames,
            "resolved_duration_seconds": resolved_frames / contract.fps,
            "duration_seconds": scene.duration_seconds,
            "steps": contract.steps,
            "cfg": profile_cfg,
            "fps": contract.fps,
            "assets": asset_snapshot,
            "output_node": workflow_record.profile.get("output_node"),
        }
        generation.input_snapshot["generation_intent"] = intent.model_dump(mode="json")
        generation.input_snapshot["director_execution"] = director_spec.model_dump(mode="json")
        generation.input_snapshot["provider"] = "minimax_h3_director"
        generation.input_snapshot["semantic_hash"] = _stable_hash(generation.input_snapshot)
        if persist:
            await self.persist_prepared(session, generation, video)
        return generation

    @staticmethod
    async def persist_prepared(
        session: AsyncSession, generation: SceneGeneration, video: Video
    ) -> None:
        """Persist an already validated snapshot without recomputing prompts or seeds."""
        record = await session.get(WorkflowRecord, generation.workflow_id)
        if record is None:
            raise AppError("WORKFLOW_NOT_FOUND", "Workflow was not found", 404)
        require_director_execution(record)
        snapshot = generation.input_snapshot
        unsigned = {key: value for key, value in snapshot.items() if key != "semantic_hash"}
        if generation.video_id != video.id or snapshot.get("semantic_hash") != _stable_hash(
            unsigned
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID", "Prepared generation changed after preflight", 422
            )
        require_frozen(snapshot, record)
        session.add(generation)
        await session.flush()
        for asset in generation.input_snapshot.get("assets", []):
            session.add(
                GenerationAsset(
                    generation_id=generation.id,
                    asset_id=asset["id"],
                    role=asset["role"],
                    order_index=asset["order_index"],
                )
            )
        if video.status not in {"ASSEMBLING", "DIRTY"}:
            video.status = "GENERATING"
