"""Prepare immutable generation snapshots before external execution begins."""

from __future__ import annotations

import secrets
from dataclasses import fields
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    GenerationAsset,
    Product,
    Project,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
)
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.domain_guards import require_active_brand
from apps.api.app.services.h3_validator import (
    H3Profile,
    H3Request,
    H3ValidationError,
    H3Validator,
)
from apps.api.app.services.prompt_engine import PromptEngine
from apps.api.app.services.workflow_registry import ApprovedWorkflow, WorkflowSlotError
from apps.api.app.services.workflow_router import (
    RoutingInput,
    WorkflowRoutingError,
    select_mode,
)

ASPECT_DIMENSIONS = {
    "9:16": (480, 864),
    "16:9": (864, 480),
    "1:1": (768, 768),
}


class GenerationService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.prompt_engine = PromptEngine(settings)

    @staticmethod
    def _ids(values: list[Any]) -> list[str]:
        return [str(value) for value in values]

    async def _load_assets(
        self, session: AsyncSession, request: GenerationRequest
    ) -> tuple[list[tuple[str, int, Asset]], list[float]]:
        requested: list[tuple[str, int, str, str]] = []
        if request.first_frame_asset_id:
            requested.append(("FIRST_FRAME", 0, str(request.first_frame_asset_id), "IMAGE"))
        if request.last_frame_asset_id:
            requested.append(("LAST_FRAME", 0, str(request.last_frame_asset_id), "IMAGE"))
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
                await session.scalars(
                    select(Asset).where(Asset.id.in_(ids)).with_for_update()
                )
            ).all()
        }
        resolved: list[tuple[str, int, Asset]] = []
        audio_durations: list[float] = []
        for role, order, asset_id, expected_kind in requested:
            asset = assets.get(asset_id)
            if asset is None or asset.deleted_at is not None:
                raise AppError("ASSET_NOT_FOUND", f"Asset {asset_id} was not found", 404)
            if asset.status != "READY" or not asset.checksum:
                raise AppError("ASSET_NOT_READY", f"Asset {asset_id} is not ready", 409)
            if asset.kind != expected_kind:
                raise AppError(
                    "GENERATION_INPUT_INVALID",
                    f"{role} requires a {expected_kind.lower()} asset",
                    422,
                    {"asset_id": asset_id, "actual_kind": asset.kind},
                )
            if role == "REFERENCE_AUDIO":
                if asset.duration_seconds is None:
                    raise AppError(
                        "ASSET_METADATA_MISSING",
                        f"Audio asset {asset_id} has no duration metadata",
                        409,
                    )
                audio_durations.append(asset.duration_seconds)
            resolved.append((role, order, asset))
        return resolved, audio_durations

    async def _workflow(
        self, session: AsyncSession, mode: str, workflow_id: Any | None
    ) -> tuple[WorkflowRecord, ApprovedWorkflow]:
        if workflow_id:
            record = await session.get(WorkflowRecord, str(workflow_id))
            if record is None:
                raise AppError("WORKFLOW_NOT_FOUND", "Workflow was not found", 404)
            if not record.enabled:
                raise AppError("WORKFLOW_NOT_APPROVED", "Workflow is not approved", 409)
            if record.mode != mode:
                raise AppError(
                    "WORKFLOW_MODE_CONFLICT",
                    "Workflow mode does not match generation inputs",
                    422,
                )
        else:
            record = await session.scalar(
                select(WorkflowRecord)
                .where(WorkflowRecord.mode == mode, WorkflowRecord.enabled.is_(True))
                .order_by(WorkflowRecord.approved_at.desc(), WorkflowRecord.created_at.desc())
                .limit(1)
            )
            if record is None:
                raise AppError(
                    "WORKFLOW_NOT_CONFIGURED",
                    f"No approved workflow is configured for {mode}",
                    409,
                )
        approved = ApprovedWorkflow(
            mode=record.mode,
            version=record.version,
            workflow=record.workflow,
            slots={
                name: (str(binding[0]), str(binding[1])) for name, binding in record.slots.items()
            },
            required_slots=frozenset(record.required_slots or []),
        )
        try:
            approved.validate()
        except WorkflowSlotError as exc:
            raise AppError("WORKFLOW_INVALID", str(exc), 409) from exc
        if (
            approved.workflow_hash != record.workflow_hash
            or approved.slot_map_hash != record.slot_map_hash
        ):
            raise AppError(
                "WORKFLOW_INTEGRITY_FAILED",
                "Approved workflow content no longer matches its hashes",
                409,
            )
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
    def _slot_values(
        approved: ApprovedWorkflow,
        *,
        prompt: str,
        negative_prompt: str,
        seed: int,
        width: int,
        height: int,
        frames: int,
        duration: float,
        steps: int,
        aspect_ratio: str,
        output_prefix: str,
        assets: list[tuple[str, int, Asset]],
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        aspect_values = profile.get("aspect_ratio_values", {})
        common: dict[str, Any] = {
            "PROMPT": prompt,
            "NEGATIVE_PROMPT": negative_prompt,
            "SEED": seed,
            "WIDTH": width,
            "HEIGHT": height,
            "FRAMES": frames,
            "LENGTH": frames,
            "DURATION": duration,
            "STEPS": steps,
            "FPS": int(profile.get("fps", 24)),
            "ASPECT_RATIO": aspect_values.get(aspect_ratio, aspect_ratio),
            "OUTPUT_PREFIX": output_prefix,
        }
        values = {name: common[name] for name in approved.slots if name in common}
        counters: dict[str, int] = {}
        for role, _order, _asset in assets:
            counters[role] = counters.get(role, 0) + 1
            name = f"{role}_{counters[role]}" if role.startswith("REFERENCE_") else role
            if name in approved.slots:
                values[name] = "__STAGED_BY_DISPATCHER__"
        return values

    async def create(
        self,
        session: AsyncSession,
        *,
        scene_id: str,
        request: GenerationRequest,
        user_id: str,
        request_id: str | None,
    ) -> SceneGeneration:
        scene_ref = await session.get(Scene, scene_id)
        if scene_ref is None:
            raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
        video = await session.get(Video, scene_ref.video_id, with_for_update=True)
        if video is None:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        scene = await session.scalar(
            select(Scene).where(Scene.id == scene_id).with_for_update()
        )
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
        width, height = (
            (request.width, request.height)
            if request.width and request.height
            else ASPECT_DIMENSIONS[video.aspect_ratio]
        )
        assets, audio_durations = await self._load_assets(session, request)
        workflow_record, approved = await self._workflow(session, mode, request.workflow_id)
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
                    reference_audio_durations=audio_durations,
                )
            )
        except H3ValidationError as exc:
            raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc

        parent_id = str(request.parent_generation_id) if request.parent_generation_id else None
        if request.operation == "VARIATION":
            parent = await session.get(SceneGeneration, parent_id)
            if parent is None or parent.scene_id != scene.id:
                raise AppError(
                    "PARENT_GENERATION_INVALID",
                    "Variation parent must belong to the same scene",
                    422,
                )
            if parent.status != "COMPLETED":
                raise AppError(
                    "PARENT_GENERATION_NOT_READY",
                    "Variation parent must be completed",
                    409,
                )

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
        execution_prompt = request.execution_prompt or prompt_result.execution_prompt
        if mode == "r2v":
            tags = []
            tags.extend(
                f"<Picture {index}>"
                for index in range(1, len(request.reference_image_asset_ids) + 1)
            )
            tags.extend(
                f"<Video {index}>" for index in range(1, len(request.reference_video_asset_ids) + 1)
            )
            tags.extend(
                f"<Audio {index}>" for index in range(1, len(request.reference_audio_asset_ids) + 1)
            )
            execution_prompt = f"References: {', '.join(tags)}\n{execution_prompt}"

        generation_no = (
            await session.scalar(
                select(func.max(SceneGeneration.generation_no)).where(
                    SceneGeneration.scene_id == scene.id
                )
            )
            or 0
        ) + 1
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode=mode,
            workflow_id=workflow_record.id,
            generation_no=generation_no,
            operation=request.operation,
            parent_generation_id=parent_id,
            status="CREATED",
            phase="PENDING",
            request_id=request_id,
            created_by=user_id,
        )
        session.add(generation)
        await session.flush()

        seed = request.seed if request.seed is not None else secrets.randbelow(2**63)
        slot_values = self._slot_values(
            approved,
            prompt=execution_prompt,
            negative_prompt=scene.negative_prompt,
            seed=seed,
            width=validated.width,
            height=validated.height,
            frames=validated.frames,
            duration=scene.duration_seconds,
            steps=request.steps,
            aspect_ratio=video.aspect_ratio,
            output_prefix=f"studio/{video.id}/{scene.id}/{generation.id}",
            assets=assets,
            profile=workflow_record.profile,
        )
        try:
            patched = approved.patch(slot_values)
        except WorkflowSlotError as exc:
            raise AppError("WORKFLOW_INPUT_INVALID", str(exc), 422) from exc

        asset_snapshot = []
        for role, order, asset in assets:
            extension = Path(asset.filename).suffix.lower()
            safe_filename = f"{asset.id}{extension}"
            asset_snapshot.append(
                {
                    "id": asset.id,
                    "object_key": asset.object_key,
                    "checksum": asset.checksum,
                    "role": role,
                    "order_index": order,
                    "filename": safe_filename,
                    "content_type": asset.content_type,
                }
            )
            session.add(
                GenerationAsset(
                    generation_id=generation.id,
                    asset_id=asset.id,
                    role=role,
                    order_index=order,
                )
            )
        generation.input_snapshot = {
            "schema_version": 1,
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
            "frames": validated.frames,
            "duration_seconds": scene.duration_seconds,
            "steps": request.steps,
            "assets": asset_snapshot,
            "output_node": workflow_record.profile.get("output_node"),
        }
        if video.status not in {"ASSEMBLING", "DIRTY"}:
            video.status = "GENERATING"
        return generation
