"""Safe, owner-only logical deletion requests for scenes."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    DeletionOperation,
    DirectorRunMember,
    FinalVideoScene,
    GenerationAsset,
    GenerationAttempt,
    Scene,
    SceneGeneration,
    Video,
    utcnow,
)
from apps.api.app.services.deletion_state import (
    DELETION_HIDDEN_STATUSES,
    is_logically_deleted,
)
from apps.api.app.services.video_dependency_guard import (
    lock_video_dependencies,
    read_video_dependencies,
)

_ACTIVE_STATES = frozenset(
    {"CREATED", "DISPATCHING", "QUEUED", "RUNNING", "COLLECTING", "CANCEL_REQUESTED"}
)
_CONFIRMATION_DEPENDENCY_KEYS = (
    "video_id",
    "video_revision",
    "video_current_final_video_id",
    "scene_snapshot",
    "generation_rows",
    "generation_asset_rows",
    "director_member_rows",
    "final_video_scene_rows",
    "output_assets",
    "affected_counts",
)
_PREVIEW_FIELDS = (
    "resource_type",
    "resource_id",
    "video_id",
    "name",
    "revision",
    "allowed",
    "reason",
    "blockers",
    "affected_counts",
    "shared_assets_preserved",
    "cleanup_eligible_outputs",
    "cleanup_blocked_outputs",
    "current",
    "async_cleanup",
    "confirmation_token",
)


def _not_found() -> AppError:
    return AppError("SCENE_NOT_FOUND", "Scene was not found", 404)


async def _existing_operation(
    session: AsyncSession, scene_id: str, user_id: str, *, is_admin: bool = False
) -> DeletionOperation | None:
    operation = await session.scalar(
        select(DeletionOperation).where(
            DeletionOperation.resource_type == "SCENE",
            DeletionOperation.resource_id == scene_id,
        )
    )
    if operation is None:
        return None
    if not is_admin and operation.resource_owner_id != user_id:
        raise _not_found()
    return operation


def _already_deleted_preview(
    operation: DeletionOperation, *, name: str, revision: int
) -> dict[str, Any]:
    preview = dict(operation.preview_snapshot or {})
    pending = operation.status == "DELETE_REQUESTED"
    shared_count = preview.get("shared_assets_preserved", 0)
    if not isinstance(shared_count, int) or isinstance(shared_count, bool):
        shared_count = 0
    preview.update(
        {
            "resource_type": "SCENE",
            "resource_id": operation.resource_id,
            "video_id": operation.video_id,
            "name": name,
            "revision": revision,
            "allowed": False,
            "reason": (
                "A deletion request is already pending."
                if pending
                else "Deletion has already been requested."
            ),
            "blockers": [
                {
                    "code": "DELETION_OPERATION_PENDING"
                    if pending
                    else "ALREADY_LOGICALLY_DELETED",
                    "label": "A deletion request is already pending"
                    if pending
                    else "Deletion has already been requested",
                    "count": 1,
                }
            ],
            "shared_assets_preserved": max(0, shared_count),
            "async_cleanup": True,
        }
    )
    preview.pop("confirmation_token", None)
    preview.setdefault("affected_counts", {})
    preview.setdefault("cleanup_eligible_outputs", 0)
    preview.setdefault("cleanup_blocked_outputs", 0)
    preview.setdefault("current", None)
    return {key: preview[key] for key in _PREVIEW_FIELDS if key in preview}


async def _count(session: AsyncSession, statement) -> int:
    return int(await session.scalar(statement) or 0)


async def _snapshot_rows(session: AsyncSession, statement) -> list[dict[str, Any]]:
    result = await session.execute(statement)
    return [dict(row) for row in result.mappings()]


def _confirmation_token(
    resource_type: str,
    resource_id: str,
    revision: int,
    dependencies: dict[str, Any],
) -> str:
    payload = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "revision": revision,
        "dependencies": {key: dependencies[key] for key in _CONFIRMATION_DEPENDENCY_KEYS},
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def _load_owned_scene(
    session: AsyncSession, scene_id: str, user_id: str, *, is_admin: bool = False
) -> tuple[Scene, Video]:
    query = select(Scene, Video).join(Video, Video.id == Scene.video_id).where(Scene.id == scene_id)
    if not is_admin:
        query = query.where(Video.created_by == user_id)
    row = await session.execute(query)
    result = row.one_or_none()
    if result is None:
        raise _not_found()
    return result[0], result[1]


async def _scene_output_ids(session: AsyncSession, scene_id: str) -> set[str]:
    output_ids: set[str] = set()
    queries = (
        select(SceneGeneration.output_asset_id).where(
            SceneGeneration.scene_id == scene_id,
            SceneGeneration.output_asset_id.is_not(None),
        ),
        select(GenerationAsset.asset_id)
        .join(SceneGeneration, SceneGeneration.id == GenerationAsset.generation_id)
        .where(
            SceneGeneration.scene_id == scene_id,
            GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
        ),
        select(DirectorRunMember.output_asset_id).where(
            DirectorRunMember.scene_id == scene_id,
            DirectorRunMember.output_asset_id.is_not(None),
        ),
        select(FinalVideoScene.asset_id).where(FinalVideoScene.scene_id == scene_id),
    )
    for query in queries:
        output_ids.update(value for value in await session.scalars(query) if value)
    return output_ids


async def _shared_scene_output_ids(
    session: AsyncSession, scene_id: str, output_ids: set[str]
) -> set[str]:
    if not output_ids:
        return set()
    shared: set[str] = set()
    queries = (
        select(GenerationAsset.asset_id)
        .join(SceneGeneration, SceneGeneration.id == GenerationAsset.generation_id)
        .where(GenerationAsset.asset_id.in_(output_ids), SceneGeneration.scene_id != scene_id),
        select(SceneGeneration.output_asset_id).where(
            SceneGeneration.output_asset_id.in_(output_ids), SceneGeneration.scene_id != scene_id
        ),
        select(DirectorRunMember.output_asset_id).where(
            DirectorRunMember.output_asset_id.in_(output_ids),
            DirectorRunMember.scene_id != scene_id,
        ),
        select(FinalVideoScene.asset_id).where(FinalVideoScene.asset_id.in_(output_ids)),
    )
    for query in queries:
        shared.update(value for value in await session.scalars(query) if value)
    return shared


async def _output_cleanup_counts(
    session: AsyncSession,
    output_ids: set[str],
    shared_ids: set[str],
    owner_id: str,
) -> tuple[int, int, int, list[dict[str, Any]]]:
    if not output_ids:
        return 0, 0, 0, []
    rows = (
        await session.execute(
            select(
                Asset.id,
                Asset.status,
                Asset.deleted_at,
                Asset.purged_at,
                Asset.resource_owner_id,
                Asset.operation_claim_id,
                Asset.operation_claim_type,
            )
            .where(Asset.id.in_(output_ids))
            .order_by(Asset.id)
        )
    ).all()
    by_id = {row.id: row for row in rows}
    eligible = 0
    blocked = 0
    output_write_claims = 0
    asset_snapshots = []
    for asset_id in sorted(output_ids):
        asset = by_id.get(asset_id)
        if asset and asset.operation_claim_type == "OUTPUT_WRITE":
            output_write_claims += 1
        asset_snapshots.append(
            {
                "id": asset_id,
                "exists": asset is not None,
                "status": asset.status if asset is not None else None,
                "deleted": asset.deleted_at is not None if asset is not None else None,
                "purged": asset.purged_at is not None if asset is not None else None,
                "owner_matches": (
                    asset.resource_owner_id == owner_id if asset is not None else False
                ),
                "has_claim": (
                    bool(asset.operation_claim_id or asset.operation_claim_type)
                    if asset is not None
                    else False
                ),
                "claim_type": asset.operation_claim_type if asset is not None else None,
                "shared": asset_id in shared_ids,
            }
        )
        if (
            asset is not None
            and asset_id not in shared_ids
            and asset.status == "READY"
            and asset.deleted_at is None
            and asset.purged_at is None
            and asset.resource_owner_id == owner_id
            and asset.operation_claim_id is None
            and asset.operation_claim_type is None
        ):
            eligible += 1
        else:
            blocked += 1
    return eligible, blocked, output_write_claims, asset_snapshots


async def _scene_assessment(
    session: AsyncSession, scene: Scene, video: Video
) -> tuple[dict[str, Any], dict[str, Any]]:
    scene_id = scene.id
    generation_ids = sorted(
        await session.scalars(
            select(SceneGeneration.id).where(SceneGeneration.scene_id == scene_id)
        )
    )
    generation_count = len(generation_ids)
    generation_attempt_count = (
        await _count(
            session,
            select(func.count(GenerationAttempt.id)).where(
                GenerationAttempt.generation_id.in_(generation_ids)
            ),
        )
        if generation_ids
        else 0
    )
    director_member_count = await _count(
        session,
        select(func.count(DirectorRunMember.id)).where(DirectorRunMember.scene_id == scene_id),
    )
    final_scene_count = await _count(
        session,
        select(func.count(FinalVideoScene.id)).where(FinalVideoScene.scene_id == scene_id),
    )
    active_generations = await _count(
        session,
        select(func.count(SceneGeneration.id)).where(
            SceneGeneration.scene_id == scene_id,
            SceneGeneration.status.in_(_ACTIVE_STATES),
        ),
    )
    output_ids = await _scene_output_ids(session, scene_id)
    shared_ids = await _shared_scene_output_ids(session, scene_id, output_ids)
    (
        eligible_outputs,
        blocked_outputs,
        output_write_claims,
        output_asset_snapshots,
    ) = await _output_cleanup_counts(session, output_ids, shared_ids, video.created_by)
    blockers = []
    for code, label, count in (
        ("ACTIVE_GENERATIONS", "Generations are still running", active_generations),
        (
            "DIRECTOR_RUN_REFERENCES",
            "A Director run still refers to this scene",
            director_member_count,
        ),
        (
            "FINAL_VIDEO_REFERENCES",
            "An assembled video still refers to this scene",
            final_scene_count,
        ),
        ("OUTPUT_WRITE_CLAIMS", "Generated files are still being written", output_write_claims),
    ):
        if count:
            blockers.append({"code": code, "label": label, "count": count})

    selected = None
    if scene.selected_generation_id:
        row = await session.execute(
            select(
                SceneGeneration.id,
                SceneGeneration.generation_no,
                SceneGeneration.status,
            ).where(
                SceneGeneration.id == scene.selected_generation_id,
                SceneGeneration.scene_id == scene_id,
            )
        )
        current = row.one_or_none()
        if current is not None:
            selected = {
                "generation_id": current.id,
                "generation_no": current.generation_no,
                "status": current.status,
            }
    affected_counts = {
        "scenes": 1,
        "generations": generation_count,
        "generation_attempts": generation_attempt_count,
        "director_run_members": director_member_count,
        "final_video_scenes": final_scene_count,
        "output_assets": len(output_ids),
        "shared_output_assets": len(shared_ids),
    }
    generation_rows = await _snapshot_rows(
        session,
        select(
            SceneGeneration.id,
            SceneGeneration.revision,
            SceneGeneration.status,
            SceneGeneration.output_asset_id,
            SceneGeneration.parent_generation_id,
        )
        .where(SceneGeneration.scene_id == scene_id)
        .order_by(SceneGeneration.id),
    )
    generation_asset_rows = await _snapshot_rows(
        session,
        select(
            GenerationAsset.id,
            GenerationAsset.generation_id,
            GenerationAsset.asset_id,
            GenerationAsset.role,
            GenerationAsset.order_index,
        )
        .join(SceneGeneration, SceneGeneration.id == GenerationAsset.generation_id)
        .where(SceneGeneration.scene_id == scene_id)
        .order_by(GenerationAsset.id),
    )
    director_member_rows = await _snapshot_rows(
        session,
        select(
            DirectorRunMember.id,
            DirectorRunMember.director_run_id,
            DirectorRunMember.scene_generation_id,
            DirectorRunMember.status,
            DirectorRunMember.output_asset_id,
        )
        .where(DirectorRunMember.scene_id == scene_id)
        .order_by(DirectorRunMember.id),
    )
    final_video_scene_rows = await _snapshot_rows(
        session,
        select(
            FinalVideoScene.id,
            FinalVideoScene.final_video_id,
            FinalVideoScene.generation_id,
            FinalVideoScene.asset_id,
        )
        .where(FinalVideoScene.scene_id == scene_id)
        .order_by(FinalVideoScene.id),
    )
    scene_snapshot = {
        "scene_order": scene.scene_order,
        "selected_generation_id": scene.selected_generation_id,
    }
    preview = {
        "resource_type": "SCENE",
        "resource_id": scene.id,
        "video_id": video.id,
        "name": f"Scene {scene.scene_order + 1}",
        "revision": scene.revision,
        "allowed": not blockers,
        "reason": None
        if not blockers
        else "Resolve active work and references before deleting this scene.",
        "blockers": blockers,
        "affected_counts": affected_counts,
        "shared_assets_preserved": len(shared_ids),
        "cleanup_eligible_outputs": eligible_outputs,
        "cleanup_blocked_outputs": blocked_outputs,
        "current": selected,
        "async_cleanup": True,
    }
    member_ids = sorted(
        await session.scalars(
            select(DirectorRunMember.id).where(DirectorRunMember.scene_id == scene_id)
        )
    )
    final_scene_ids = sorted(
        await session.scalars(
            select(FinalVideoScene.id).where(FinalVideoScene.scene_id == scene_id)
        )
    )
    dependencies = {
        "video_id": video.id,
        "video_revision": video.revision,
        "video_current_final_video_id": video.current_final_video_id,
        "scene_snapshot": scene_snapshot,
        "generation_rows": generation_rows,
        "generation_asset_rows": generation_asset_rows,
        "director_member_rows": director_member_rows,
        "final_video_scene_rows": final_video_scene_rows,
        "output_assets": output_asset_snapshots,
        "generation_ids": generation_ids,
        "director_run_member_ids": member_ids,
        "final_video_scene_ids": final_scene_ids,
        "output_asset_ids": sorted(output_ids),
        "shared_output_asset_ids": sorted(shared_ids),
        "affected_counts": affected_counts,
    }
    if preview["allowed"]:
        preview["confirmation_token"] = _confirmation_token(
            "SCENE", scene.id, scene.revision, dependencies
        )
    return preview, dependencies


async def _lock_video_graph(session: AsyncSession, video: Video, user_id: str) -> None:
    try:
        await lock_video_dependencies(session, video, user_id=user_id, is_admin=False)
    except AppError as exc:
        # Archived business dependencies do not prevent their owner deleting a
        # child. Ownership and the row locks have already been verified by the
        # dependency guard before it reports these state-only conflicts.
        if exc.code not in {"PROJECT_NOT_ACTIVE", "PRODUCT_NOT_ACTIVE", "BRAND_NOT_ACTIVE"}:
            raise


async def _lock_scene_children(session: AsyncSession, scene_id: str) -> None:
    with session.no_autoflush:
        generation_ids = sorted(
            await session.scalars(
                select(SceneGeneration.id).where(SceneGeneration.scene_id == scene_id)
            )
        )
        for generation_id in generation_ids:
            await lock_revisioned_row(session, SceneGeneration, generation_id)

        generation_asset_ids = sorted(
            await session.scalars(
                select(GenerationAsset.id)
                .join(SceneGeneration, SceneGeneration.id == GenerationAsset.generation_id)
                .where(SceneGeneration.scene_id == scene_id)
            )
        )
        if generation_asset_ids:
            (
                await session.execute(
                    select(GenerationAsset.id)
                    .where(GenerationAsset.id.in_(generation_asset_ids))
                    .order_by(GenerationAsset.id)
                    .with_for_update()
                )
            ).all()

        member_ids = sorted(
            await session.scalars(
                select(DirectorRunMember.id).where(DirectorRunMember.scene_id == scene_id)
            )
        )
        if member_ids:
            (
                await session.execute(
                    select(DirectorRunMember.id)
                    .where(DirectorRunMember.id.in_(member_ids))
                    .order_by(DirectorRunMember.id)
                    .with_for_update()
                )
            ).all()

        final_scene_ids = sorted(
            await session.scalars(
                select(FinalVideoScene.id).where(FinalVideoScene.scene_id == scene_id)
            )
        )
        if final_scene_ids:
            (
                await session.execute(
                    select(FinalVideoScene.id)
                    .where(FinalVideoScene.id.in_(final_scene_ids))
                    .order_by(FinalVideoScene.id)
                    .with_for_update()
                )
            ).all()

        output_ids = await _scene_output_ids(session, scene_id)
        if output_ids:
            (
                await session.execute(
                    select(Asset.id)
                    .where(Asset.id.in_(output_ids))
                    .order_by(Asset.id)
                    .with_for_update()
                )
            ).all()


async def preview_scene_deletion(
    session: AsyncSession, *, scene_id: str, user_id: str, is_admin: bool = False
) -> dict:
    with session.no_autoflush:
        existing = await _existing_operation(session, scene_id, user_id, is_admin=is_admin)
        try:
            scene, video = await _load_owned_scene(session, scene_id, user_id, is_admin=is_admin)
        except AppError:
            if existing is not None:
                return _already_deleted_preview(
                    existing,
                    name="Scene",
                    revision=existing.expected_revision,
                )
            raise
        if await is_logically_deleted(session, "VIDEO", video.id):
            raise _not_found()
        await read_video_dependencies(
            session, video, user_id=video.created_by, is_admin=False, require_active=False
        )
        if existing is not None:
            return _already_deleted_preview(
                existing,
                name=f"Scene {scene.scene_order + 1}",
                revision=scene.revision,
            )
        preview, _ = await _scene_assessment(session, scene, video)
        return preview


async def request_scene_deletion(
    session: AsyncSession,
    *,
    scene_id: str,
    user_id: str,
    is_admin: bool = False,
    expected_revision: int,
    confirmation_token: str | None = None,
) -> DeletionOperation:
    """Accept deletion atomically; caller must keep this session in its transaction."""
    with session.no_autoflush:
        existing = await _existing_operation(session, scene_id, user_id, is_admin=is_admin)
        if existing is not None:
            if existing.status in DELETION_HIDDEN_STATUSES:
                return existing
            raise AppError(
                "DELETION_OPERATION_PENDING", "A deletion request is already pending", 409
            )

        query = (
            select(Scene.id, Scene.video_id)
            .join(Video, Video.id == Scene.video_id)
            .where(Scene.id == scene_id)
        )
        if not is_admin:
            query = query.where(Video.created_by == user_id)
        visible = await session.execute(query)
        scene_identity = visible.one_or_none()
        if scene_identity is None:
            raise _not_found()
        video = await lock_revisioned_row(session, Video, scene_identity.video_id)
        if video is None or (not is_admin and video.created_by != user_id):
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
        existing = await _existing_operation(session, scene_id, user_id, is_admin=is_admin)
        if existing is not None:
            if existing.status in DELETION_HIDDEN_STATUSES:
                return existing
            raise AppError(
                "DELETION_OPERATION_PENDING", "A deletion request is already pending", 409
            )
        await _lock_video_graph(session, video, video.created_by)
        if await is_logically_deleted(session, "VIDEO", video.id):
            raise _not_found()
        scene = await lock_revisioned_row(session, Scene, scene_id, immutable_fields=("video_id",))
        if scene is None or scene.video_id != video.id:
            raise _not_found()
        await _lock_scene_children(session, scene.id)

        preview, dependencies = await _scene_assessment(session, scene, video)
        if scene.revision != expected_revision:
            raise AppError("REVISION_CONFLICT", "Scene changed since preview", 412)
        expected_token = _confirmation_token("SCENE", scene.id, scene.revision, dependencies)
        token_matches = (
            isinstance(confirmation_token, str)
            and confirmation_token.isascii()
            and len(confirmation_token) == 64
            and hmac.compare_digest(confirmation_token, expected_token)
        )
        if confirmation_token is not None and not token_matches:
            raise AppError(
                "DELETION_CONFIRMATION_STALE",
                "Deletion preview changed; review it again before confirming",
                412,
            )
        if preview["blockers"]:
            raise AppError(
                "SCENE_DELETE_BLOCKED",
                "Active work or immutable references must be resolved before deleting this scene",
                409,
                {"blockers": preview["blockers"]},
            )
        if not token_matches:
            raise AppError(
                "DELETION_CONFIRMATION_STALE",
                "Deletion preview changed; review it again before confirming",
                412,
            )

        now = utcnow()
        operation = DeletionOperation(
            resource_type="SCENE",
            resource_id=scene.id,
            video_id=video.id,
            resource_owner_id=video.created_by,
            requested_by_id=user_id,
            status="LOGICALLY_DELETED",
            expected_revision=expected_revision,
            preview_snapshot={
                key: value for key, value in preview.items() if key != "confirmation_token"
            },
            dependency_snapshot=dependencies,
            requested_at=now,
            logically_deleted_at=now,
        )
        session.add(operation)
        await session.flush()
        return operation
