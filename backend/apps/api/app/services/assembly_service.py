"""Create immutable, content-addressed assembly manifests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.ownership import get_owned, get_video_output_asset, owner_scope
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    FinalVideoScene,
    Scene,
    SceneGeneration,
    Video,
)
from apps.api.app.schemas.api import AssemblyRequest
from apps.api.app.services.delivery_presets import resolve_delivery
from apps.api.app.services.generation_freshness import (
    is_generation_fresh,
    is_selected_execution_group_fresh,
)
from apps.api.app.services.video_dependency_guard import (
    lock_video_dependencies,
    read_video_dependencies,
)


def manifest_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class AssemblyService:
    async def authorize_inputs(
        self,
        session: AsyncSession,
        *,
        video_id: str,
        request: AssemblyRequest,
        user_id: str,
        is_admin: bool = False,
    ) -> None:
        """Validate ownership of every referenced input before route side effects.

        ``create`` repeats these checks while holding the established locks. This
        preflight lets callers reject cross-owner references before claiming an
        idempotency key, while the locked checks remain authoritative for writes.
        """
        video = await get_owned(
            session,
            Video,
            video_id,
            user_id,
            error_code="VIDEO_NOT_FOUND",
            message="Video was not found",
            allow_all=is_admin,
        )
        await read_video_dependencies(session, video, user_id=user_id, is_admin=is_admin)
        scenes = list(
            (
                await session.scalars(
                    select(Scene).where(
                        Scene.video_id == video.id,
                        Scene.enabled.is_(True),
                        owner_scope(Scene, user_id, allow_all=is_admin),
                    )
                )
            ).all()
        )
        for scene in scenes:
            if not scene.selected_generation_id:
                continue
            generation = await get_owned(
                session,
                SceneGeneration,
                scene.selected_generation_id,
                user_id,
                error_code="GENERATION_NOT_FOUND",
                message="Generation was not found",
                allow_all=is_admin,
            )
            if generation.scene_id != scene.id:
                raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
            if generation.output_asset_id:
                await get_video_output_asset(
                    session,
                    video,
                    generation.output_asset_id,
                    user_id,
                    role="GENERATED_VIDEO",
                    generation_id=generation.id,
                    allow_all=is_admin,
                )

        if request.background_audio_asset_id:
            await get_owned(
                session,
                Asset,
                str(request.background_audio_asset_id),
                user_id,
                error_code="ASSET_NOT_FOUND",
                message="Asset was not found",
                allow_all=is_admin,
            )

    async def create(
        self,
        session: AsyncSession,
        *,
        video_id: str,
        request: AssemblyRequest,
        expected_revision: int,
        user_id: str,
        request_id: str | None,
        is_admin: bool = False,
    ) -> FinalVideo:
        with session.no_autoflush:
            video = await get_owned(
                session, Video, video_id, user_id,
                error_code="VIDEO_NOT_FOUND", message="Video was not found", allow_all=is_admin,
            )
        dependencies = await lock_video_dependencies(
            session, video, user_id=user_id, is_admin=is_admin
        )
        video = dependencies.video
        if video.revision != expected_revision:
            raise AppError(
                "REVISION_CONFLICT",
                "Video changed since it was loaded",
                412,
                {"expected": expected_revision, "actual": video.revision},
            )
        active = await session.scalar(
            select(FinalVideo.id)
            .where(
                FinalVideo.video_id == video.id,
                FinalVideo.status.in_({"QUEUED", "ASSEMBLING", "CANCEL_REQUESTED"}),
            )
            .limit(1)
            .execution_options(autoflush=False)
        )
        if active:
            raise AppError(
                "ASSEMBLY_ALREADY_ACTIVE",
                "This video already has an active assembly",
                409,
            )
        scenes = list(
            (
                await session.scalars(
                    select(Scene)
                    .where(
                        Scene.video_id == video.id,
                        Scene.enabled.is_(True),
                        owner_scope(Scene, user_id, allow_all=is_admin),
                    )
                    .order_by(Scene.scene_order, Scene.id)
                    .with_for_update()
                    .execution_options(autoflush=False)
                )
            ).all()
        )
        if not scenes:
            raise AppError("ASSEMBLY_EMPTY", "Video has no enabled scenes", 409)
        # All Scene locks precede Generation/Asset locks. Refresh through the
        # revision guard so compatible pending edits retain their history.
        scenes = [
            await lock_revisioned_row(session, Scene, scene.id, immutable_fields=("video_id",))
            for scene in scenes
        ]

        scene_entries: list[dict[str, Any]] = []
        rows: list[tuple[Scene, SceneGeneration, Asset]] = []
        for scene in scenes:
            if not scene.selected_generation_id:
                raise AppError(
                    "SCENE_SELECTION_REQUIRED",
                    "Every enabled scene must have a selected generation",
                    409,
                    {"scene_id": scene.id},
                )
            generation = await session.scalar(
                select(SceneGeneration).where(
                    SceneGeneration.id == scene.selected_generation_id,
                    owner_scope(SceneGeneration, user_id, allow_all=is_admin),
                ).execution_options(autoflush=False)
            )
            if generation is not None:
                generation = await lock_revisioned_row(
                    session, SceneGeneration, generation.id,
                    immutable_fields=("video_id", "scene_id"),
                )
            if generation is None or generation.scene_id != scene.id:
                raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
            if generation.status != "COMPLETED" or not generation.output_asset_id:
                raise AppError(
                    "SCENE_GENERATION_NOT_READY",
                    "A selected scene generation is not complete",
                    409,
                    {"scene_id": scene.id},
                )
            with session.no_autoflush:
                fresh = await is_generation_fresh(
                    session, scene, video, generation
                ) and await is_selected_execution_group_fresh(session, scene, video, generation)
            if not fresh:
                raise AppError(
                    "SCENE_SELECTION_STALE",
                    "A selected generation no longer matches current scene inputs",
                    409,
                    {"scene_id": scene.id, "generation_id": generation.id},
                )
            with session.no_autoflush:
                asset = await get_video_output_asset(
                    session,
                    video,
                    generation.output_asset_id,
                    user_id,
                    role="GENERATED_VIDEO",
                    generation_id=generation.id,
                    for_update=True,
                    allow_all=is_admin,
                )
            if (
                asset.status != "READY"
                or not asset.checksum
                or asset.deleted_at is not None
            ):
                raise AppError(
                    "SCENE_ASSET_NOT_READY",
                    "A selected scene output is unavailable",
                    409,
                    {"scene_id": scene.id},
                )
            transition = {
                "type": request.transition,
                "duration_seconds": (
                    request.crossfade_seconds if request.transition == "CROSSFADE" else 0
                ),
            }
            scene_entries.append(
                {
                    "scene_id": scene.id,
                    "scene_revision": scene.revision,
                    "scene_order": scene.scene_order,
                    "duration_seconds": scene.duration_seconds,
                    "generation_id": generation.id,
                    "generation_revision": generation.revision,
                    "asset_id": asset.id,
                    "asset_object_key": asset.object_key,
                    "asset_checksum": asset.checksum,
                    "asset_size_bytes": asset.size_bytes,
                    "transition": transition,
                }
            )
            rows.append((scene, generation, asset))

        background = None
        if request.background_audio_asset_id:
            asset = await session.scalar(
                select(Asset)
                .where(
                    Asset.id == str(request.background_audio_asset_id),
                    owner_scope(Asset, user_id, allow_all=is_admin),
                )
                .with_for_update()
                .execution_options(populate_existing=True, autoflush=False)
            )
            if (
                asset is None
                or asset.kind != "AUDIO"
                or asset.status != "READY"
                or not asset.checksum
                or asset.deleted_at is not None
            ):
                raise AppError(
                    "BACKGROUND_AUDIO_NOT_READY",
                    "Background audio asset is unavailable",
                    409,
                )
            if not (
                asset.project_id == video.project_id
                or (video.product_id is not None and asset.product_id == video.product_id)
            ):
                raise AppError(
                    "BACKGROUND_AUDIO_SCOPE_INVALID",
                    "Background audio must belong to this video's project or linked product",
                    422,
                )
            background = {
                "asset_id": asset.id,
                "asset_object_key": asset.object_key,
                "asset_checksum": asset.checksum,
                "asset_size_bytes": asset.size_bytes,
            }

        version_no = (
            await session.scalar(
                select(func.max(FinalVideo.version_no)).where(FinalVideo.video_id == video.id)
                .execution_options(autoflush=False)
            )
            or 0
        ) + 1
        assembly_config = request.model_dump(mode="json")
        delivery = resolve_delivery(
            preset=request.delivery_preset,
            width=request.width,
            height=request.height,
            fps=request.fps,
            fit_mode=request.fit_mode,
        )
        assembly_config.update(
            width=delivery.width,
            height=delivery.height,
            fps=delivery.fps,
            fit_mode=delivery.fit_mode,
        )
        manifest = {
            "schema_version": 1,
            "video_id": video.id,
            "video_revision": video.revision,
            "video_kind": video.kind,
            "aspect_ratio": video.aspect_ratio,
            "version_no": version_no,
            "assembly_config": assembly_config,
            "scenes": scene_entries,
            "background_audio": background,
        }
        final = FinalVideo(
            video_id=video.id,
            version_no=version_no,
            status="QUEUED",
            phase="QUEUED",
            manifest=manifest,
            manifest_hash=manifest_hash(manifest),
            assembly_config=assembly_config,
            background_audio_asset_id=(background["asset_id"] if background else None),
            request_id=request_id,
            created_by=user_id,
        )
        session.add(final)
        await session.flush()
        for (scene, generation, asset), entry in zip(rows, scene_entries, strict=True):
            session.add(
                FinalVideoScene(
                    final_video_id=final.id,
                    scene_id=scene.id,
                    scene_order=scene.scene_order,
                    generation_id=generation.id,
                    asset_id=asset.id,
                    asset_checksum=asset.checksum,
                    transition_config=entry["transition"],
                )
            )
        video.status = "ASSEMBLING"
        return final
