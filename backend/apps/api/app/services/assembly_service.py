"""Create immutable, content-addressed assembly manifests."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    FinalVideoScene,
    Product,
    Project,
    Scene,
    SceneGeneration,
    Video,
)
from apps.api.app.schemas.api import AssemblyRequest
from apps.api.app.services.domain_guards import require_active_brand


def manifest_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


class AssemblyService:
    async def create(
        self,
        session: AsyncSession,
        *,
        video_id: str,
        request: AssemblyRequest,
        expected_revision: int,
        user_id: str,
        request_id: str | None,
    ) -> FinalVideo:
        video = await session.get(Video, video_id, with_for_update=True)
        if video is None:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        if video.revision != expected_revision:
            raise AppError(
                "REVISION_CONFLICT",
                "Video changed since it was loaded",
                412,
                {"expected": expected_revision, "actual": video.revision},
            )
        project = await session.get(Project, video.project_id)
        if project is None or project.archived:
            raise AppError("PROJECT_NOT_ACTIVE", "Project is missing or archived", 409)
        product = await session.get(Product, video.product_id) if video.product_id else None
        if product is not None and product.archived:
            raise AppError("PRODUCT_NOT_ACTIVE", "Product is archived", 409)
        brand_id = video.brand_id or (product.brand_id if product else None)
        await require_active_brand(session, brand_id)
        active = await session.scalar(
            select(FinalVideo.id)
            .where(
                FinalVideo.video_id == video.id,
                FinalVideo.status.in_({"QUEUED", "ASSEMBLING", "CANCEL_REQUESTED"}),
            )
            .limit(1)
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
                    .where(Scene.video_id == video.id, Scene.enabled.is_(True))
                    .order_by(Scene.scene_order, Scene.id)
                    .with_for_update()
                )
            ).all()
        )
        if not scenes:
            raise AppError("ASSEMBLY_EMPTY", "Video has no enabled scenes", 409)

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
            generation = await session.get(SceneGeneration, scene.selected_generation_id)
            if (
                generation is None
                or generation.scene_id != scene.id
                or generation.status != "COMPLETED"
                or not generation.output_asset_id
            ):
                raise AppError(
                    "SCENE_GENERATION_NOT_READY",
                    "A selected scene generation is not complete",
                    409,
                    {"scene_id": scene.id},
                )
            asset = await session.get(Asset, generation.output_asset_id, with_for_update=True)
            if (
                asset is None
                or asset.status != "READY"
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
            asset = await session.get(
                Asset, str(request.background_audio_asset_id), with_for_update=True
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
            background = {
                "asset_id": asset.id,
                "asset_object_key": asset.object_key,
                "asset_checksum": asset.checksum,
                "asset_size_bytes": asset.size_bytes,
            }

        version_no = (
            await session.scalar(
                select(func.max(FinalVideo.version_no)).where(FinalVideo.video_id == video.id)
            )
            or 0
        ) + 1
        assembly_config = request.model_dump(mode="json")
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
