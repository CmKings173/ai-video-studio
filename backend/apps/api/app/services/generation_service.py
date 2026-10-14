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

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.ownership import get_owned, owner_scope
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    DirectorRunMember,
    GenerationAsset,
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
    ResolvedGenerationConfigurationDTO,
    SceneGenerationConfig,
)
from apps.api.app.services.continuity_groups import requires_native_execution_group
from apps.api.app.services.deletion_state import (
    is_logically_deleted,
    not_logically_deleted_predicate,
)
from apps.api.app.services.generation_dependency_invalidation import (
    lock_pending_dependency_bindings,
)
from apps.api.app.services.generation_display import resolve_generation_configuration
from apps.api.app.services.generation_freshness import (
    capture_generation_freshness,
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
from apps.api.app.services.video_dependency_guard import lock_video_dependencies
from apps.api.app.services.workflow_contracts import (
    approved_record,
    frozen_ratio,
    profile_hash,
    require_frozen,
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
            (
                await session.scalars(
                    select(Scene).where(
                        Scene.video_id == scene.video_id,
                        not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
                    )
                )
            ).all()
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
        self,
        session: AsyncSession,
        request: GenerationRequest,
        video: Video,
        *,
        user_id: str,
        is_admin: bool = False,
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
        # Asset has no revision counter. Project columns under the lock instead
        # of refreshing ORM instances, which could erase pending editor changes.
        with session.no_autoflush:
            rows = (
                (
                    await session.execute(
                        select(*Asset.__table__.c)
                        .where(
                            Asset.id.in_(ids),
                            owner_scope(Asset, user_id, allow_all=is_admin),
                        )
                        .order_by(Asset.id)
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
        assets = {row["id"]: Asset(**copy.deepcopy(dict(row))) for row in rows}
        resolved: list[tuple[str, int, Asset]] = []
        audio_durations: list[float] = []
        video_durations: list[float] = []
        for role, order, asset_id, expected_kind in requested:
            asset = assets.get(asset_id)
            if asset is None or asset.deleted_at is not None:
                raise AppError("ASSET_NOT_FOUND", f"Asset {asset_id} was not found", 404)
            self._require_asset_owner_scope(asset, video)
            # Persisted invalid state cannot be repaired by unflushed intent.
            self._require_asset_ready(asset)
            cached = session.identity_map.get(
                inspect(Asset).identity_key_from_primary_key((asset_id,))
            )
            if cached is not None:
                for column in inspect(Asset).column_attrs:
                    history = inspect(cached).attrs[column.key].history
                    if not history.has_changes():
                        continue
                    if not history.deleted or history.deleted[0] != getattr(asset, column.key):
                        raise AppError(
                            "REVISION_CONFLICT",
                            "Pending Asset edits conflict with locked state",
                            412,
                        )
                    setattr(asset, column.key, copy.deepcopy(getattr(cached, column.key)))
                if asset.deleted_at is not None:
                    raise AppError("ASSET_NOT_FOUND", f"Asset {asset_id} was not found", 404)
                self._require_asset_owner_scope(asset, video)
                self._require_asset_ready(asset)
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

    @staticmethod
    def _require_asset_ready(asset: Asset) -> None:
        if (
            asset.status != "READY"
            or not asset.checksum
            or not re.fullmatch(r"[a-f0-9]{64}", asset.checksum)
        ):
            raise AppError("ASSET_NOT_READY", f"Asset {asset.id} is not ready", 409)

    @staticmethod
    def _require_asset_owner_scope(asset: Asset, video: Video) -> None:
        if (asset.resource_owner_id or asset.created_by) != video.created_by:
            raise AppError("ASSET_NOT_FOUND", "Asset was not found", 404)
        project_matches = asset.project_id is not None and asset.project_id == video.project_id
        product_matches = asset.product_id is not None and asset.product_id == video.product_id
        if (
            (asset.project_id is not None and not project_matches)
            or (asset.product_id is not None and not product_matches)
            or not (project_matches or product_matches)
        ):
            raise AppError(
                "ASSET_SCOPE_INVALID", "Asset is outside this video project/product", 422
            )

    @staticmethod
    def _director_execution_settings(request: GenerationRequest) -> dict[str, Any]:
        motion, refine, face_refine, audio_policy = GenerationService._director_settings(request)
        return {
            "motion_context": motion,
            "refine": refine,
            "face_refine": face_refine,
            "audio_policy": audio_policy,
        }

    @staticmethod
    def require_exact_workflow_choice(request: GenerationRequest) -> None:
        if (request.width is None) != (request.height is None):
            raise AppError(
                "GENERATION_INPUT_INVALID", "width and height must be supplied together", 422
            )
        if request.workflow_id is not None and (
            request.aspect_ratio is None or request.width is None or request.height is None
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "An explicit workflow choice requires aspect_ratio, width and height",
                422,
            )

    @staticmethod
    def bind_batch_preview_configuration_hash(
        generation: SceneGeneration, batch_hash: str, *, run: Any | None = None
    ) -> None:
        if re.fullmatch(r"[a-fA-F0-9]{64}", batch_hash) is None:
            raise AppError(
                "PREVIEW_CONFIGURATION_STALE",
                "The batch preview configuration hash is invalid",
                409,
            )
        snapshot = copy.deepcopy(generation.input_snapshot)
        configuration = (
            snapshot.get("resolved_configuration") if isinstance(snapshot, dict) else None
        )
        if (
            not isinstance(snapshot, dict)
            or not isinstance(snapshot.get("resolved_configuration_hash"), str)
            or not isinstance(configuration, dict)
            or configuration.get("runnable") is not True
        ):
            raise AppError(
                "PREVIEW_CONFIGURATION_STALE",
                "The prepared generation has no frozen resolved configuration",
                409,
            )
        snapshot.pop("semantic_hash", None)
        snapshot["batch_preview_configuration_hash"] = batch_hash.lower()
        snapshot["semantic_hash"] = _stable_hash(snapshot)
        generation.input_snapshot = snapshot
        if run is not None:
            run_snapshot = copy.deepcopy(run.input_snapshot)
            members = run_snapshot.get("members") if isinstance(run_snapshot, dict) else None
            if not isinstance(members, list):
                raise AppError(
                    "DIRECTOR_GROUP_INVALID", "Prepared run has no frozen member list", 422
                )
            matches = [
                member for member in members if member.get("scene_generation_id") == generation.id
            ]
            if len(matches) != 1:
                raise AppError("DIRECTOR_GROUP_INVALID", "Prepared run membership changed", 422)
            matches[0]["input_snapshot"] = copy.deepcopy(snapshot)
            run_snapshot.pop("semantic_hash", None)
            run_snapshot["semantic_hash"] = _stable_hash(run_snapshot)
            run.input_snapshot = run_snapshot

    @staticmethod
    def finalize_aggregate_member_configurations(
        generations: list[SceneGeneration], run: Any
    ) -> None:
        """Promote non-runnable candidates only after exact aggregate qualification."""
        run_snapshot = copy.deepcopy(run.input_snapshot)
        members = run_snapshot.get("members") if isinstance(run_snapshot, dict) else None
        evidence = (
            run_snapshot.get("aggregate_qualification") if isinstance(run_snapshot, dict) else None
        )
        settings_hash = (
            run_snapshot.get("aggregate_settings_hash") if isinstance(run_snapshot, dict) else None
        )
        if (
            run_snapshot.get("kind") != "director_aggregate"
            or not isinstance(members, list)
            or len(members) != len(generations)
            or not isinstance(evidence, dict)
            or evidence.get("executed") is not True
            or evidence.get("settings_hash") != settings_hash
            or not isinstance(settings_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", settings_hash) is None
        ):
            raise AppError(
                "DIRECTOR_GROUP_INVALID", "Run has no validated aggregate qualification", 422
            )

        by_generation = {member.get("scene_generation_id"): member for member in members}
        if len(by_generation) != len(generations) or set(by_generation) != {
            generation.id for generation in generations
        }:
            raise AppError("DIRECTOR_GROUP_INVALID", "Prepared run membership changed", 422)

        model_hash = evidence.get("model_hash")
        gpu_count = evidence.get("gpu_count")
        runtime_values = (
            evidence.get("comfyui_version"),
            evidence.get("comfyui_commit"),
            evidence.get("gpu_model"),
            evidence.get("cuda_runtime"),
            evidence.get("driver_version"),
        )
        if (
            any(not isinstance(value, str) or not value for value in runtime_values)
            or type(gpu_count) is not int
            or gpu_count < 1
            or not isinstance(model_hash, str)
            or re.fullmatch(r"[a-f0-9]{64}", model_hash) is None
        ):
            raise AppError(
                "DIRECTOR_GROUP_INVALID", "Aggregate runtime evidence is incomplete", 422
            )
        runtime_identity = (
            f"ComfyUI {evidence['comfyui_version']} ({evidence['comfyui_commit'][:12]}); "
            f"{evidence['gpu_model']} x{gpu_count}; CUDA {evidence['cuda_runtime']}; "
            f"driver {evidence['driver_version']}"
        )
        aggregate_spec = run_snapshot.get("director_execution")
        if not isinstance(aggregate_spec, dict):
            raise AppError("DIRECTOR_GROUP_INVALID", "Run has no frozen execution spec", 422)

        for generation in generations:
            snapshot = copy.deepcopy(generation.input_snapshot)
            frozen_member = by_generation[generation.id]
            candidate_data = snapshot.get("resolved_configuration")
            member_spec = snapshot.get("director_execution")
            if (
                not isinstance(candidate_data, dict)
                or frozen_member.get("input_snapshot") != snapshot
                or frozen_member.get("scene_id") != generation.scene_id
                or not isinstance(member_spec, dict)
            ):
                raise AppError(
                    "DIRECTOR_GROUP_INVALID", "Run member candidate changed before promotion", 422
                )
            candidate = ResolvedGenerationConfigurationDTO.model_validate(candidate_data)
            canvas = member_spec.get("canvas")
            if not isinstance(canvas, dict) or (
                candidate.runnable
                or candidate.qualification_status != "AGGREGATE_PREPARATION"
                or not isinstance(candidate.configuration_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", candidate.configuration_hash) is None
                or member_spec.get("workflow_id") != candidate.workflow_id
                or member_spec.get("workflow_code") != candidate.workflow_code
                or member_spec.get("workflow_version") != candidate.workflow_version
                or member_spec.get("workflow_hash") != candidate.workflow_hash
                or member_spec.get("slot_map_hash") != aggregate_spec.get("slot_map_hash")
                or member_spec.get("profile_hash") != candidate.profile_hash
                or member_spec.get("requested_mode") != candidate.resolved_mode
                or member_spec.get("quality_profile") != candidate.quality_profile
                or canvas.get("aspect_ratio") != candidate.aspect_ratio
                or canvas.get("width") != candidate.native_width
                or canvas.get("height") != candidate.native_height
                or member_spec.get("fps") != candidate.fps
                or member_spec.get("frames") != candidate.frames
                or member_spec.get("resolved_duration_seconds") != candidate.duration_seconds
                or member_spec.get("steps") != candidate.sampling_steps
                or member_spec.get("workflow_id") != aggregate_spec.get("workflow_id")
                or member_spec.get("workflow_code") != aggregate_spec.get("workflow_code")
                or member_spec.get("workflow_version") != aggregate_spec.get("workflow_version")
                or member_spec.get("workflow_hash") != aggregate_spec.get("workflow_hash")
                or member_spec.get("profile_hash") != aggregate_spec.get("profile_hash")
                or member_spec.get("requested_mode") != aggregate_spec.get("requested_mode")
                or member_spec.get("quality_profile") != aggregate_spec.get("quality_profile")
                or canvas != aggregate_spec.get("canvas")
                or member_spec.get("task") != aggregate_spec.get("task")
                or member_spec.get("steps") != aggregate_spec.get("steps")
                or member_spec.get("cfg") != aggregate_spec.get("cfg")
                or member_spec.get("fps") != aggregate_spec.get("fps")
                or member_spec.get("seed_policy") != aggregate_spec.get("seed_policy")
                or member_spec.get("refine") != aggregate_spec.get("refine")
                or member_spec.get("face_refine") != aggregate_spec.get("face_refine")
                or member_spec.get("audio_policy") != aggregate_spec.get("audio_policy")
            ):
                raise AppError(
                    "DIRECTOR_GROUP_INVALID", "Aggregate member differs from its candidate", 422
                )
            member_index = frozen_member.get("member_index")
            timeline = aggregate_spec.get("timeline")
            if (
                type(member_index) is not int
                or not isinstance(timeline, list)
                or member_index >= len(timeline)
                or timeline[member_index].get("scene_id") != generation.scene_id
                or timeline[member_index].get("frame_count") != member_spec.get("frames")
                or frozen_member.get("frame_count") != member_spec.get("frames")
            ):
                raise AppError("DIRECTOR_GROUP_INVALID", "Aggregate member timing changed", 422)

            final_hash = _stable_hash(
                {
                    "generate_all_member_configuration_v1": {
                        "candidate_configuration_hash": candidate.configuration_hash,
                        "aggregate_settings_hash": settings_hash,
                        "scene_id": generation.scene_id,
                        "member_index": member_index,
                        "workflow_id": candidate.workflow_id,
                        "workflow_version": candidate.workflow_version,
                        "workflow_hash": candidate.workflow_hash,
                        "profile_hash": candidate.profile_hash,
                        "resolved_mode": candidate.resolved_mode,
                        "quality_profile": candidate.quality_profile,
                        "aspect_ratio": candidate.aspect_ratio,
                        "native_canvas": [candidate.native_width, candidate.native_height],
                        "expected_output_canvas": [
                            candidate.expected_output_width,
                            candidate.expected_output_height,
                        ],
                        "steps": member_spec["steps"],
                        "cfg": member_spec["cfg"],
                        "fps": member_spec["fps"],
                        "frames": member_spec["frames"],
                        "duration_seconds": member_spec["requested_duration_seconds"],
                        "resolved_duration_seconds": member_spec["resolved_duration_seconds"],
                        "seed_policy": member_spec["seed_policy"],
                    }
                }
            )
            final_configuration = candidate.model_copy(
                update={
                    "duration_seconds": member_spec["resolved_duration_seconds"],
                    "frames": member_spec["frames"],
                    "fps": member_spec["fps"],
                    "runtime_identity": runtime_identity,
                    "model_identity": f"sha256:{model_hash}",
                    "qualification_status": "POC_EXECUTED",
                    "runnable": True,
                    "unavailable_reason": None,
                    "configuration_hash": final_hash,
                },
                deep=True,
            )
            snapshot["resolved_configuration"] = final_configuration.model_dump(mode="json")
            snapshot["resolved_configuration_hash"] = final_hash
            snapshot["aggregate_settings_hash"] = settings_hash
            snapshot.pop("semantic_hash", None)
            snapshot["semantic_hash"] = _stable_hash(snapshot)
            generation.input_snapshot = snapshot
            frozen_member["input_snapshot"] = copy.deepcopy(snapshot)

        run_snapshot.pop("semantic_hash", None)
        run_snapshot["semantic_hash"] = _stable_hash(run_snapshot)
        run.input_snapshot = run_snapshot

    @staticmethod
    def _require_runnable_configuration(
        configuration: ResolvedGenerationConfigurationDTO,
        *,
        requested_width: int | None,
        requested_height: int | None,
        allow_aggregate_candidate: bool = False,
    ) -> None:
        is_candidate = (
            allow_aggregate_candidate
            and not configuration.runnable
            and configuration.qualification_status == "AGGREGATE_PREPARATION"
        )
        if not configuration.runnable and not is_candidate:
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                configuration.unavailable_reason or "Resolved workflow is not runnable",
                409,
            )
        if (
            not isinstance(configuration.configuration_hash, str)
            or re.fullmatch(r"[a-fA-F0-9]{64}", configuration.configuration_hash) is None
        ):
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                "Qualified workflow did not resolve an immutable configuration hash",
                409,
            )
        native_width, native_height = configuration.native_width, configuration.native_height
        if native_width is None or native_height is None:
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                "Qualified workflow did not resolve an exact native canvas",
                409,
            )
        if (
            configuration.sampling_steps is None
            or configuration.fps is None
            or configuration.frames is None
            or configuration.duration_seconds is None
        ):
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                "Qualified workflow did not resolve exact sampling and timing values",
                409,
            )
        if requested_width is not None and (requested_width, requested_height) != (
            native_width,
            native_height,
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Requested canvas differs from the exact qualified workflow choice",
                422,
            )

    @staticmethod
    def _require_preview_configuration_hash(
        request: GenerationRequest,
        configuration: ResolvedGenerationConfigurationDTO,
    ) -> None:
        if (
            request.execution_prompt is None
            or configuration.qualification_status == "AGGREGATE_PREPARATION"
        ):
            return
        supplied_hash = getattr(request, "preview_configuration_hash", None)
        current_hash = configuration.configuration_hash
        if (
            not isinstance(supplied_hash, str)
            or re.fullmatch(r"[a-fA-F0-9]{64}", supplied_hash) is None
            or not isinstance(current_hash, str)
            or re.fullmatch(r"[a-fA-F0-9]{64}", current_hash) is None
            or not secrets.compare_digest(supplied_hash.lower(), current_hash.lower())
        ):
            raise AppError(
                "PREVIEW_CONFIGURATION_STALE",
                "The accepted prompt preview is missing or no longer matches the live "
                "generation configuration; refresh the preview",
                409,
            )

    @staticmethod
    def _effective_seed_policy(request: GenerationRequest) -> str:
        return request.seed_policy or ("FIXED" if request.seed is not None else "RANDOM")

    async def _workflow(
        self,
        session: AsyncSession,
        mode: str,
        workflow_id: Any | None,
        quality: str = "STANDARD",
        ratio: str | None = None,
        *,
        requested_mode: str | None = None,
        requested_width: int | None = None,
        requested_height: int | None = None,
        execution_settings: dict[str, Any] | None = None,
        duration_seconds: float,
        seed_policy: str,
        seed: int | None,
        allow_aggregate: bool = False,
    ) -> tuple[WorkflowRecord, ApprovedWorkflow, ResolvedGenerationConfigurationDTO]:
        if ratio is None:
            raise AppError("GENERATION_INPUT_INVALID", "An aspect ratio is required", 422)
        if (requested_width is None) != (requested_height is None):
            raise AppError(
                "GENERATION_INPUT_INVALID", "width and height must be supplied together", 422
            )
        execution_scope = "aggregate" if allow_aggregate else "single_scene"
        actual_seed_policy = seed_policy
        fixed_seed = seed if actual_seed_policy == "FIXED" else None
        configuration = await resolve_generation_configuration(
            session,
            requested_mode=mode,
            quality_profile=quality,
            aspect_ratio=ratio,
            duration_seconds=duration_seconds,
            execution_scope=execution_scope,
            workflow_id=str(workflow_id) if workflow_id is not None else None,
            seed_policy=actual_seed_policy,
            fixed_seed=fixed_seed,
            director_settings=execution_settings or {},
            aggregate_preparation=allow_aggregate,
        )
        aggregate_candidate = (
            allow_aggregate
            and not configuration.runnable
            and configuration.qualification_status == "AGGREGATE_PREPARATION"
        )
        if (
            not configuration.runnable and not aggregate_candidate
        ) or not configuration.workflow_id:
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                configuration.unavailable_reason or "No qualified workflow matches the request",
                409,
            )
        self._require_runnable_configuration(
            configuration,
            requested_width=requested_width,
            requested_height=requested_height,
            allow_aggregate_candidate=aggregate_candidate,
        )
        record = await session.scalar(
            select(WorkflowRecord)
            .where(WorkflowRecord.id == configuration.workflow_id)
            .with_for_update()
        )
        if record is not None:
            await session.refresh(record, with_for_update=True)
        if (
            record is None
            or not record.enabled
            or record.mode != configuration.resolved_mode
            or record.quality_profile != quality
            or record.execution_scope != execution_scope
            or record.workflow_hash != configuration.workflow_hash
            or record.version != configuration.workflow_version
            or record.code != configuration.workflow_code
        ):
            raise AppError(
                "WORKFLOW_CONFIGURATION_STALE",
                "The selected workflow changed while resolving this configuration; "
                "refresh and retry",
                409,
            )
        confirmed = await resolve_generation_configuration(
            session,
            requested_mode=mode,
            quality_profile=quality,
            aspect_ratio=ratio,
            duration_seconds=duration_seconds,
            execution_scope=execution_scope,
            workflow_id=str(record.id),
            seed_policy=actual_seed_policy,
            fixed_seed=fixed_seed,
            director_settings=execution_settings or {},
            aggregate_preparation=allow_aggregate,
        )
        if (
            confirmed.runnable != configuration.runnable
            or confirmed.qualification_status != configuration.qualification_status
            or confirmed.configuration_hash != configuration.configuration_hash
            or confirmed.workflow_hash != record.workflow_hash
        ):
            raise AppError(
                "WORKFLOW_CONFIGURATION_STALE",
                "The qualified workflow configuration changed during admission; refresh and retry",
                409,
            )
        configuration = confirmed
        require_director_execution(record)
        approved = approved_record(record)
        configuration.requested_mode = requested_mode or mode
        return record, approved, configuration

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
        is_admin: bool = False,
        defer_aggregate_qualification: bool = False,
    ):
        self.require_exact_workflow_choice(request)
        parent = await get_owned(
            session,
            SceneGeneration,
            str(request.parent_generation_id),
            user_id,
            error_code="GENERATION_NOT_FOUND",
            message="Generation was not found",
            allow_all=is_admin,
        )
        if parent.scene_id != scene.id:
            raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
        freshness = (parent.input_snapshot or {}).get("generation_freshness") or {}
        if freshness.get("execution_group") or await session.scalar(
            select(DirectorRunMember.id).where(
                DirectorRunMember.scene_generation_id == parent.id,
                owner_scope(DirectorRunMember, user_id, allow_all=is_admin),
            )
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
        self.require_exact_workflow_choice(inherited)
        seed_policy = self._effective_seed_policy(inherited)
        record, target, configuration = await self._workflow(
            session,
            mode,
            inherited.workflow_id,
            quality,
            ratio,
            requested_mode=inherited.mode or "AUTO",
            requested_width=inherited.width,
            requested_height=inherited.height,
            execution_settings=self._director_execution_settings(inherited),
            duration_seconds=float(frozen["duration_seconds"]),
            seed_policy=seed_policy,
            seed=inherited.seed,
            allow_aggregate=defer_aggregate_qualification,
        )
        self._require_preview_configuration_hash(inherited, configuration)
        assets, audio, videos = await self._load_assets(
            session, inherited, video, user_id=user_id, is_admin=is_admin
        )
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
        width, height = configuration.native_width, configuration.native_height
        if inherited.steps is not None and inherited.steps != configuration.sampling_steps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw steps contradict qualified profile", 422
            )
        if inherited.fps is not None and inherited.fps != configuration.fps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested FPS contradicts qualified profile", 422
            )
        resolved_frames = configuration.frames
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
            "steps": configuration.sampling_steps,
            "frames": resolved_frames,
            "resolved_duration_seconds": resolved_frames / configuration.fps,
            "cfg": profile_cfg,
            "fps": configuration.fps,
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
        resolved_width, resolved_height = configuration.native_width, configuration.native_height
        resolved_frames = configuration.frames
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
            fps=configuration.fps,
            steps=configuration.sampling_steps,
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
            mode=mode,
            base_workflow=copy.deepcopy(record.workflow),
            slots=copy.deepcopy(record.slots),
            required_slots=copy.deepcopy(record.required_slots),
            workflow_id=record.id,
            workflow_code=record.code,
            workflow_hash=record.workflow_hash,
            slot_map_hash=record.slot_map_hash,
            workflow_version=record.version,
            runtime_profile=copy.deepcopy(record.profile),
            profile_hash=profile_hash(record.profile),
            requested_aspect_ratio=ratio,
            requested_quality_profile=quality,
            generation_intent=intent.model_dump(mode="json"),
            director_execution=director_spec.model_dump(mode="json"),
            provider="minimax_h3_director",
            workflow=copy.deepcopy(record.workflow),
            resolved_width=resolved_width,
            resolved_height=resolved_height,
            width=resolved_width,
            height=resolved_height,
            frames=resolved_frames,
            resolved_duration_seconds=resolved_frames / configuration.fps,
            steps=configuration.sampling_steps,
            cfg=profile_cfg,
            fps=configuration.fps,
        )
        snapshot.update(
            workflow=graph,
            seed=seed,
            seed_policy=seed_policy,
            resolved_configuration=configuration.model_dump(mode="json"),
            resolved_configuration_hash=configuration.configuration_hash,
            preview_configuration_hash=getattr(inherited, "preview_configuration_hash", None),
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
        is_admin: bool = False,
        preflight_seed: int | None = None,
    ) -> SceneGeneration:
        if defer_aggregate_qualification and persist:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Aggregate preparation candidates may not be persisted "
                "before DirectorRun qualification",
                422,
            )
        if preflight_seed is not None and (
            persist
            or type(preflight_seed) is not int
            or not 0 <= preflight_seed < JS_SAFE_SEED_BOUND
            or request.seed is not None
            or request.seed_policy == "FIXED"
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Preflight seed is only valid for non-persisting RANDOM preparation",
                422,
            )
        with session.no_autoflush:
            video_id = await session.scalar(
                select(Scene.video_id)
                .join(Video, Video.id == Scene.video_id)
                .where(
                    Scene.id == scene_id,
                    owner_scope(Video, user_id, allow_all=is_admin),
                    not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
                )
            )
        if video_id is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        with session.no_autoflush:
            await lock_pending_dependency_bindings(session)
            video_id = await session.scalar(
                select(Scene.video_id)
                .join(Video, Video.id == Scene.video_id)
                .where(
                    Scene.id == scene_id,
                    owner_scope(Video, user_id, allow_all=is_admin),
                    not_logically_deleted_predicate("SCENE", Scene.id, Scene.video_id),
                )
            )
        if video_id is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        video = await lock_revisioned_row(session, Video, video_id)
        if video is None:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        if not is_admin and video.created_by != user_id:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        dependencies = await lock_video_dependencies(
            session, video, user_id=user_id, is_admin=is_admin
        )
        product, brand = dependencies.product, dependencies.effective_brand
        scene = await lock_revisioned_row(session, Scene, scene_id, immutable_fields=("video_id",))
        if scene is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        if await is_logically_deleted(session, "SCENE", scene.id, video.id):
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
        if request.operation in {"VARIATION", "REGENERATE"}:
            if not persist:
                raise AppError(
                    "GENERATION_INPUT_INVALID", "Batch preparation requires ORIGINAL", 422
                )
            return await self._from_parent(
                session,
                scene,
                video,
                request,
                user_id,
                request_id,
                is_admin=is_admin,
                defer_aggregate_qualification=defer_aggregate_qualification,
            )
        self.require_exact_workflow_choice(request)
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
        seed_policy = self._effective_seed_policy(request)
        workflow_record, approved, configuration = await self._workflow(
            session,
            mode,
            request.workflow_id,
            quality,
            ratio,
            requested_mode=request.mode or "AUTO",
            requested_width=request.width,
            requested_height=request.height,
            execution_settings=self._director_execution_settings(request),
            duration_seconds=float(scene.duration_seconds),
            seed_policy=seed_policy,
            seed=request.seed,
            allow_aggregate=defer_aggregate_qualification,
        )
        self._require_preview_configuration_hash(request, configuration)
        width, height = configuration.native_width, configuration.native_height
        if request.steps is not None and request.steps != configuration.sampling_steps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Raw steps contradict the qualified profile", 422
            )
        if request.fps is not None and request.fps != configuration.fps:
            raise AppError(
                "GENERATION_INPUT_INVALID", "Requested FPS contradicts the qualified profile", 422
            )
        resolved_frames = configuration.frames
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
        assets, audio_durations, video_durations = await self._load_assets(
            session, request, video, user_id=user_id, is_admin=is_admin
        )
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
        seed = (
            request.seed
            if request.seed is not None
            else preflight_seed
            if preflight_seed is not None
            else secrets.randbelow(JS_SAFE_SEED_BOUND)
        )
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
            fps=configuration.fps,
            steps=configuration.sampling_steps,
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
            "seed_policy": seed_policy,
            "resolved_configuration": configuration.model_dump(mode="json"),
            "resolved_configuration_hash": configuration.configuration_hash,
            "preview_configuration_hash": getattr(request, "preview_configuration_hash", None),
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
            "resolved_duration_seconds": resolved_frames / configuration.fps,
            "duration_seconds": scene.duration_seconds,
            "steps": configuration.sampling_steps,
            "cfg": profile_cfg,
            "fps": configuration.fps,
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
        session: AsyncSession,
        generation: SceneGeneration,
        video: Video,
        *,
        director_run: Any | None = None,
    ) -> None:
        """Persist an already validated snapshot without recomputing prompts or seeds."""
        record = await session.get(WorkflowRecord, generation.workflow_id)
        if record is None:
            raise AppError("WORKFLOW_NOT_FOUND", "Workflow was not found", 404)
        require_director_execution(record)
        snapshot = generation.input_snapshot
        resolved_configuration = snapshot.get("resolved_configuration")
        if (
            not isinstance(resolved_configuration, dict)
            or resolved_configuration.get("runnable") is not True
            or resolved_configuration.get("qualification_status") == "AGGREGATE_PREPARATION"
            or resolved_configuration.get("configuration_hash")
            != snapshot.get("resolved_configuration_hash")
        ):
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "Only a final runnable configuration may be persisted",
                422,
            )
        if record.execution_scope == "aggregate":
            freshness = snapshot.get("generation_freshness")
            group = freshness.get("execution_group") if isinstance(freshness, dict) else None
            aggregate_hash = snapshot.get("aggregate_settings_hash")
            batch_hash = snapshot.get("batch_preview_configuration_hash")
            scene_ids = group.get("scene_ids") if isinstance(group, dict) else None
            generation_ids = group.get("generation_ids") if isinstance(group, dict) else None
            run_snapshot = (
                director_run.input_snapshot
                if director_run is not None and isinstance(director_run.input_snapshot, dict)
                else None
            )
            run_members = run_snapshot.get("members") if isinstance(run_snapshot, dict) else None
            run_unsigned = (
                {key: value for key, value in run_snapshot.items() if key != "semantic_hash"}
                if isinstance(run_snapshot, dict)
                else None
            )
            matching_members = (
                [
                    member
                    for member in run_members
                    if isinstance(member, dict)
                    and member.get("scene_generation_id") == generation.id
                ]
                if isinstance(run_members, list)
                else []
            )
            run_qualification = (
                run_snapshot.get("aggregate_qualification")
                if isinstance(run_snapshot, dict)
                else None
            )
            if (
                resolved_configuration.get("qualification_status") != "POC_EXECUTED"
                or not isinstance(aggregate_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", aggregate_hash) is None
                or not isinstance(batch_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", batch_hash) is None
                or not isinstance(group, dict)
                or not isinstance(group.get("run_id"), str)
                or not isinstance(scene_ids, list)
                or generation.scene_id not in scene_ids
                or not isinstance(generation_ids, list)
                or generation.id not in generation_ids
                or director_run is None
                or director_run.video_id != video.id
                or director_run.workflow_id != generation.workflow_id
                or director_run.id != group.get("run_id")
                or not isinstance(run_snapshot, dict)
                or run_snapshot.get("kind") != "director_aggregate"
                or run_snapshot.get("execution_group") != group
                or run_snapshot.get("aggregate_settings_hash") != aggregate_hash
                or not isinstance(run_qualification, dict)
                or run_qualification.get("executed") is not True
                or run_qualification.get("settings_hash") != aggregate_hash
                or run_snapshot.get("semantic_hash") != _stable_hash(run_unsigned)
                or len(matching_members) != 1
                or matching_members[0].get("scene_id") != generation.scene_id
                or matching_members[0].get("input_snapshot") != snapshot
                or any(not isinstance(member, dict) for member in run_members)
                or len(run_members) != len(generation_ids)
                or [member.get("scene_generation_id") for member in run_members] != generation_ids
                or [member.get("scene_id") for member in run_members] != scene_ids
                or any(
                    not isinstance(member.get("input_snapshot"), dict)
                    or member["input_snapshot"].get("aggregate_settings_hash") != aggregate_hash
                    or member["input_snapshot"].get("batch_preview_configuration_hash")
                    != batch_hash
                    or not isinstance(member["input_snapshot"].get("resolved_configuration"), dict)
                    or member["input_snapshot"]["resolved_configuration"].get("runnable")
                    is not True
                    or member["input_snapshot"]["resolved_configuration"].get(
                        "qualification_status"
                    )
                    != "POC_EXECUTED"
                    for member in run_members
                )
            ):
                raise AppError(
                    "GENERATION_INPUT_INVALID",
                    "Aggregate generations require a qualified, finalized Generate All run",
                    422,
                )
        elif director_run is not None:
            raise AppError(
                "GENERATION_INPUT_INVALID",
                "A DirectorRun may only be persisted with its aggregate members",
                422,
            )
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
