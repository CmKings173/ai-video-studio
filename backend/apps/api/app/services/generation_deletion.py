"""Owner-only logical deletion requests for generated video versions."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Asset,
    DeletionOperation,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
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
_ACTIVE_FINAL_STATES = frozenset({"QUEUED", "ASSEMBLING", "CANCEL_REQUESTED"})
_CONFIRMATION_DEPENDENCY_KEYS = (
    "video_id",
    "video_revision",
    "video_current_final_video_id",
    "scene_rows",
    "generation_rows",
    "generation_asset_rows",
    "generation_attempt_rows",
    "director_run_rows",
    "director_member_rows",
    "final_video_rows",
    "final_video_scene_rows",
    "target_generation_id",
    "target_scene_id",
    "output_assets",
    "output_asset_ids",
    "shared_output_asset_ids",
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
    return AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)


async def _existing_operation(
    session: AsyncSession, generation_id: str, user_id: str, *, is_admin: bool = False
) -> DeletionOperation | None:
    operation = await session.scalar(
        select(DeletionOperation).where(
            DeletionOperation.resource_type == "GENERATION",
            DeletionOperation.resource_id == generation_id,
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
            "resource_type": "GENERATION",
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
    generation_id: str,
    revision: int,
    dependencies: dict[str, Any],
) -> str:
    payload = {
        "resource_type": "GENERATION",
        "resource_id": generation_id,
        "revision": revision,
        "dependencies": {key: dependencies[key] for key in _CONFIRMATION_DEPENDENCY_KEYS},
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def _load_owned_generation(
    session: AsyncSession, generation_id: str, user_id: str, *, is_admin: bool = False
) -> tuple[SceneGeneration, Scene, Video]:
    query = (
        select(SceneGeneration, Scene, Video)
        .join(
            Scene,
            and_(
                Scene.id == SceneGeneration.scene_id,
                Scene.video_id == SceneGeneration.video_id,
            ),
        )
        .join(Video, Video.id == SceneGeneration.video_id)
        .where(SceneGeneration.id == generation_id)
    )
    if not is_admin:
        query = query.where(Video.created_by == user_id)
    result = await session.execute(query)
    row = result.one_or_none()
    if row is None:
        raise _not_found()
    return row[0], row[1], row[2]


async def _generation_output_ids(session: AsyncSession, generation_id: str) -> set[str]:
    output_ids = {
        value
        for value in await session.scalars(
            select(SceneGeneration.output_asset_id).where(
                SceneGeneration.id == generation_id,
                SceneGeneration.output_asset_id.is_not(None),
            )
        )
        if value
    }
    output_ids.update(
        value
        for value in await session.scalars(
            select(GenerationAsset.asset_id).where(
                GenerationAsset.generation_id == generation_id,
                GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
            )
        )
        if value
    )
    return output_ids


async def _shared_output_ids(
    session: AsyncSession, generation_id: str, output_ids: set[str]
) -> set[str]:
    if not output_ids:
        return set()
    shared: set[str] = set()
    queries = (
        select(SceneGeneration.output_asset_id).where(
            SceneGeneration.output_asset_id.in_(output_ids),
            SceneGeneration.id != generation_id,
        ),
        select(GenerationAsset.asset_id).where(
            GenerationAsset.asset_id.in_(output_ids),
            GenerationAsset.generation_id != generation_id,
        ),
        select(DirectorRunMember.output_asset_id).where(
            DirectorRunMember.output_asset_id.in_(output_ids),
            DirectorRunMember.output_asset_id.is_not(None),
        ),
        select(FinalVideo.output_asset_id).where(
            FinalVideo.output_asset_id.in_(output_ids),
            FinalVideo.output_asset_id.is_not(None),
        ),
        select(FinalVideo.background_audio_asset_id).where(
            FinalVideo.background_audio_asset_id.in_(output_ids),
            FinalVideo.background_audio_asset_id.is_not(None),
        ),
        select(FinalVideoScene.asset_id).where(FinalVideoScene.asset_id.in_(output_ids)),
    )
    for query in queries:
        shared.update(value for value in await session.scalars(query) if value)
    return shared


async def _output_cleanup_counts(
    session: AsyncSession,
    *,
    generation: SceneGeneration,
    video: Video,
    output_ids: set[str],
    shared_ids: set[str],
) -> tuple[int, int, int, list[dict[str, Any]]]:
    if not output_ids:
        return 0, 0, 0, []

    generation_output = exists(
        select(1).where(
            SceneGeneration.id == generation.id,
            SceneGeneration.output_asset_id == Asset.id,
        )
    )
    director_output = exists(
        select(1).where(
            GenerationAsset.generation_id == generation.id,
            GenerationAsset.asset_id == Asset.id,
            GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
        )
    )
    provenance_safe = and_(
        or_(generation_output, director_output),
        Asset.kind == "VIDEO",
        Asset.role == "GENERATED_VIDEO",
        Asset.ownership_provenance == "VIDEO_PARENT",
        Asset.resource_owner_id == video.created_by,
        Asset.project_id == video.project_id,
    )
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
                Asset.ownership_provenance,
                Asset.project_id,
                Asset.kind,
                Asset.role,
                provenance_safe.label("provenance_safe"),
            )
            .where(Asset.id.in_(output_ids))
            .order_by(Asset.id)
        )
    ).all()
    by_id = {row.id: row for row in rows}
    eligible = 0
    blocked = 0
    output_write_claims = 0
    snapshots = []
    for asset_id in sorted(output_ids):
        asset = by_id.get(asset_id)
        if asset is not None and asset.operation_claim_type == "OUTPUT_WRITE":
            output_write_claims += 1
        shared = asset_id in shared_ids
        safe = bool(asset is not None and asset.provenance_safe)
        snapshots.append(
            {
                "id": asset_id,
                "exists": asset is not None,
                "status": asset.status if asset is not None else None,
                "deleted": asset.deleted_at is not None if asset is not None else None,
                "purged": asset.purged_at is not None if asset is not None else None,
                "owner_matches": (
                    asset.resource_owner_id == video.created_by if asset is not None else False
                ),
                "provenance_safe": safe,
                "ownership_provenance": (asset.ownership_provenance if asset is not None else None),
                "project_matches": (
                    asset.project_id == video.project_id if asset is not None else False
                ),
                "kind": asset.kind if asset is not None else None,
                "role": asset.role if asset is not None else None,
                "has_claim": (
                    bool(asset.operation_claim_id or asset.operation_claim_type)
                    if asset is not None
                    else False
                ),
                "claim_type": asset.operation_claim_type if asset is not None else None,
                "shared": shared,
            }
        )
        if (
            asset is not None
            and safe
            and not shared
            and asset.status == "READY"
            and asset.deleted_at is None
            and asset.purged_at is None
            and asset.operation_claim_id is None
            and asset.operation_claim_type is None
        ):
            eligible += 1
        else:
            blocked += 1
    return eligible, blocked, output_write_claims, snapshots


async def _generation_assessment(
    session: AsyncSession,
    generation: SceneGeneration,
    scene: Scene,
    video: Video,
) -> tuple[dict[str, Any], dict[str, Any]]:
    generation_id = generation.id
    child_generation_count = await _count(
        session,
        select(func.count(SceneGeneration.id)).where(
            SceneGeneration.parent_generation_id == generation_id
        ),
    )
    attempt_count = await _count(
        session,
        select(func.count(GenerationAttempt.id)).where(
            GenerationAttempt.generation_id == generation_id
        ),
    )
    generation_asset_count = await _count(
        session,
        select(func.count(GenerationAsset.id)).where(
            GenerationAsset.generation_id == generation_id
        ),
    )
    director_member_count = await _count(
        session,
        select(func.count(DirectorRunMember.id)).where(
            DirectorRunMember.scene_generation_id == generation_id
        ),
    )
    final_scene_count = await _count(
        session,
        select(func.count(FinalVideoScene.id)).where(
            FinalVideoScene.generation_id == generation_id
        ),
    )
    active_generations = await _count(
        session,
        select(func.count(SceneGeneration.id)).where(
            SceneGeneration.video_id == video.id,
            SceneGeneration.status.in_(_ACTIVE_STATES),
        ),
    )
    active_director_runs = await _count(
        session,
        select(func.count(DirectorRun.id)).where(
            DirectorRun.video_id == video.id,
            DirectorRun.status.in_(_ACTIVE_STATES),
        ),
    )
    active_assemblies = await _count(
        session,
        select(func.count(FinalVideo.id)).where(
            FinalVideo.video_id == video.id,
            FinalVideo.status.in_(_ACTIVE_FINAL_STATES),
        ),
    )
    selected = scene.selected_generation_id == generation_id
    output_ids = await _generation_output_ids(session, generation_id)
    shared_ids = await _shared_output_ids(session, generation_id, output_ids)
    (
        eligible_outputs,
        blocked_outputs,
        output_write_claims,
        output_asset_snapshots,
    ) = await _output_cleanup_counts(
        session,
        generation=generation,
        video=video,
        output_ids=output_ids,
        shared_ids=shared_ids,
    )

    blockers = []
    for code, label, count in (
        ("ACTIVE_GENERATIONS", "Generation work is still running", active_generations),
        ("ACTIVE_DIRECTOR_RUNS", "Director runs are still running", active_director_runs),
        ("ACTIVE_ASSEMBLIES", "Video assembly is still running", active_assemblies),
        ("SELECTED_GENERATION", "This generation is selected for its scene", int(selected)),
        (
            "CHILD_GENERATION_REFERENCES",
            "A later generation is based on this generation",
            child_generation_count,
        ),
        (
            "DIRECTOR_RUN_REFERENCES",
            "A Director run still refers to this generation",
            director_member_count,
        ),
        (
            "FINAL_VIDEO_REFERENCES",
            "An assembled video still refers to this generation",
            final_scene_count,
        ),
        ("OUTPUT_WRITE_CLAIMS", "Generated files are still being written", output_write_claims),
    ):
        if count:
            blockers.append({"code": code, "label": label, "count": count})

    current = {
        "scene_id": scene.id,
        "generation_id": generation_id,
        "generation_no": generation.generation_no,
        "status": generation.status,
        "selected": selected,
    }
    affected_counts = {
        "generations": 1,
        "child_generations": child_generation_count,
        "generation_attempts": attempt_count,
        "generation_assets": generation_asset_count,
        "director_run_members": director_member_count,
        "final_video_scenes": final_scene_count,
        "output_assets": len(output_ids),
        "shared_output_assets": len(shared_ids),
    }

    scene_rows = await _snapshot_rows(
        session,
        select(Scene.id, Scene.revision, Scene.scene_order, Scene.selected_generation_id)
        .where(Scene.video_id == video.id)
        .order_by(Scene.id),
    )
    generation_rows = await _snapshot_rows(
        session,
        select(
            SceneGeneration.id,
            SceneGeneration.scene_id,
            SceneGeneration.revision,
            SceneGeneration.status,
            SceneGeneration.output_asset_id,
            SceneGeneration.parent_generation_id,
        )
        .where(SceneGeneration.video_id == video.id)
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
        .where(SceneGeneration.video_id == video.id)
        .order_by(GenerationAsset.id),
    )
    generation_attempt_rows = await _snapshot_rows(
        session,
        select(GenerationAttempt.id, GenerationAttempt.generation_id, GenerationAttempt.status)
        .join(SceneGeneration, SceneGeneration.id == GenerationAttempt.generation_id)
        .where(SceneGeneration.video_id == video.id)
        .order_by(GenerationAttempt.id),
    )
    director_run_rows = await _snapshot_rows(
        session,
        select(DirectorRun.id, DirectorRun.revision, DirectorRun.status)
        .where(DirectorRun.video_id == video.id)
        .order_by(DirectorRun.id),
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
        .join(DirectorRun, DirectorRun.id == DirectorRunMember.director_run_id)
        .where(DirectorRun.video_id == video.id)
        .order_by(DirectorRunMember.id),
    )
    final_video_rows = await _snapshot_rows(
        session,
        select(
            FinalVideo.id,
            FinalVideo.revision,
            FinalVideo.status,
            FinalVideo.output_asset_id,
            FinalVideo.background_audio_asset_id,
        )
        .where(FinalVideo.video_id == video.id)
        .order_by(FinalVideo.id),
    )
    final_video_scene_rows = await _snapshot_rows(
        session,
        select(
            FinalVideoScene.id,
            FinalVideoScene.final_video_id,
            FinalVideoScene.scene_id,
            FinalVideoScene.generation_id,
            FinalVideoScene.asset_id,
        )
        .join(FinalVideo, FinalVideo.id == FinalVideoScene.final_video_id)
        .where(FinalVideo.video_id == video.id)
        .order_by(FinalVideoScene.id),
    )
    preview = {
        "resource_type": "GENERATION",
        "resource_id": generation.id,
        "video_id": video.id,
        "name": f"Generation {generation.generation_no}",
        "revision": generation.revision,
        "allowed": not blockers,
        "reason": (
            None
            if not blockers
            else "Resolve active work and downstream references before deleting this generation."
        ),
        "blockers": blockers,
        "affected_counts": affected_counts,
        "shared_assets_preserved": len(shared_ids),
        "cleanup_eligible_outputs": eligible_outputs,
        "cleanup_blocked_outputs": blocked_outputs,
        "current": current,
        "async_cleanup": True,
    }
    dependencies = {
        "video_id": video.id,
        "video_revision": video.revision,
        "video_current_final_video_id": video.current_final_video_id,
        "scene_rows": scene_rows,
        "generation_rows": generation_rows,
        "generation_asset_rows": generation_asset_rows,
        "generation_attempt_rows": generation_attempt_rows,
        "director_run_rows": director_run_rows,
        "director_member_rows": director_member_rows,
        "final_video_rows": final_video_rows,
        "final_video_scene_rows": final_video_scene_rows,
        "target_generation_id": generation.id,
        "target_scene_id": scene.id,
        "output_assets": output_asset_snapshots,
        "output_asset_ids": sorted(output_ids),
        "shared_output_asset_ids": sorted(shared_ids),
        "affected_counts": affected_counts,
    }
    if preview["allowed"]:
        preview["confirmation_token"] = _confirmation_token(
            generation.id, generation.revision, dependencies
        )
    return preview, dependencies


async def _lock_video_graph(session: AsyncSession, video: Video, user_id: str) -> None:
    try:
        await lock_video_dependencies(session, video, user_id=user_id, is_admin=False)
    except AppError as exc:
        if exc.code not in {"PROJECT_NOT_ACTIVE", "PRODUCT_NOT_ACTIVE", "BRAND_NOT_ACTIVE"}:
            raise


async def _lock_video_children(session: AsyncSession, video_id: str, generation_id: str) -> None:
    """Lock child rows in the same stable order used by video deletion."""
    with session.no_autoflush:
        scene_ids = sorted(
            await session.scalars(select(Scene.id).where(Scene.video_id == video_id))
        )
        for scene_id in scene_ids:
            await lock_revisioned_row(session, Scene, scene_id, immutable_fields=("video_id",))

        generation_ids = sorted(
            await session.scalars(
                select(SceneGeneration.id).where(SceneGeneration.video_id == video_id)
            )
        )
        for identity in generation_ids:
            await lock_revisioned_row(
                session,
                SceneGeneration,
                identity,
                immutable_fields=("video_id", "scene_id"),
            )

        generation_asset_ids = sorted(
            await session.scalars(
                select(GenerationAsset.id)
                .join(SceneGeneration, SceneGeneration.id == GenerationAsset.generation_id)
                .where(SceneGeneration.video_id == video_id)
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

        attempt_ids = sorted(
            await session.scalars(
                select(GenerationAttempt.id)
                .join(SceneGeneration, SceneGeneration.id == GenerationAttempt.generation_id)
                .where(SceneGeneration.video_id == video_id)
            )
        )
        if attempt_ids:
            (
                await session.execute(
                    select(GenerationAttempt.id)
                    .where(GenerationAttempt.id.in_(attempt_ids))
                    .order_by(GenerationAttempt.id)
                    .with_for_update()
                )
            ).all()

        director_run_ids = sorted(
            await session.scalars(select(DirectorRun.id).where(DirectorRun.video_id == video_id))
        )
        for identity in director_run_ids:
            await lock_revisioned_row(session, DirectorRun, identity)

        member_ids = sorted(
            await session.scalars(
                select(DirectorRunMember.id)
                .join(DirectorRun, DirectorRun.id == DirectorRunMember.director_run_id)
                .where(DirectorRun.video_id == video_id)
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

        final_video_ids = sorted(
            await session.scalars(select(FinalVideo.id).where(FinalVideo.video_id == video_id))
        )
        for identity in final_video_ids:
            await lock_revisioned_row(session, FinalVideo, identity)

        final_scene_ids = sorted(
            await session.scalars(
                select(FinalVideoScene.id)
                .join(FinalVideo, FinalVideo.id == FinalVideoScene.final_video_id)
                .where(FinalVideo.video_id == video_id)
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

        output_ids = await _generation_output_ids(session, generation_id)
        if output_ids:
            (
                await session.execute(
                    select(Asset.id)
                    .where(Asset.id.in_(output_ids))
                    .order_by(Asset.id)
                    .with_for_update()
                )
            ).all()


async def preview_generation_deletion(
    session: AsyncSession, *, generation_id: str, user_id: str, is_admin: bool = False
) -> dict[str, Any]:
    with session.no_autoflush:
        existing = await _existing_operation(session, generation_id, user_id, is_admin=is_admin)
        try:
            generation, scene, video = await _load_owned_generation(
                session, generation_id, user_id, is_admin=is_admin
            )
        except AppError:
            if existing is not None:
                stored_name = (existing.preview_snapshot or {}).get("name", "Generation")
                return _already_deleted_preview(
                    existing, name=stored_name, revision=existing.expected_revision
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
                name=f"Generation {generation.generation_no}",
                revision=generation.revision,
            )
        preview, _ = await _generation_assessment(session, generation, scene, video)
        return preview


async def request_generation_deletion(
    session: AsyncSession,
    *,
    generation_id: str,
    user_id: str,
    is_admin: bool = False,
    expected_revision: int,
    confirmation_token: str | None = None,
) -> DeletionOperation:
    """Accept deletion atomically; the caller owns the transaction boundary."""
    with session.no_autoflush:
        existing = await _existing_operation(session, generation_id, user_id, is_admin=is_admin)
        if existing is not None:
            if existing.status in DELETION_HIDDEN_STATUSES:
                return existing
            raise AppError(
                "DELETION_OPERATION_PENDING", "A deletion request is already pending", 409
            )

        query = (
            select(SceneGeneration.id, SceneGeneration.video_id)
            .join(Video, Video.id == SceneGeneration.video_id)
            .where(SceneGeneration.id == generation_id)
        )
        if not is_admin:
            query = query.where(Video.created_by == user_id)
        identity = await session.execute(query)
        row = identity.one_or_none()
        if row is None:
            raise _not_found()
        video = await lock_revisioned_row(
            session,
            Video,
            row.video_id,
            immutable_fields=("created_by", "project_id"),
        )
        if video is None or (not is_admin and video.created_by != user_id):
            raise _not_found()
        existing = await _existing_operation(session, generation_id, user_id, is_admin=is_admin)
        if existing is not None:
            if existing.status in DELETION_HIDDEN_STATUSES:
                return existing
            raise AppError(
                "DELETION_OPERATION_PENDING", "A deletion request is already pending", 409
            )

        await _lock_video_graph(session, video, video.created_by)
        if await is_logically_deleted(session, "VIDEO", video.id):
            raise _not_found()
        await _lock_video_children(session, video.id, generation_id)
        generation = await lock_revisioned_row(
            session,
            SceneGeneration,
            generation_id,
            immutable_fields=("video_id", "scene_id"),
        )
        if generation is None or generation.video_id != video.id:
            raise _not_found()
        scene = await lock_revisioned_row(
            session, Scene, generation.scene_id, immutable_fields=("video_id",)
        )
        if scene is None or scene.video_id != video.id:
            raise _not_found()

        # The locks refresh these objects and fence the dependency rows until
        # the caller commits this operation.
        preview, dependencies = await _generation_assessment(session, generation, scene, video)
        if generation.revision != expected_revision:
            raise AppError("REVISION_CONFLICT", "Generation changed since preview", 412)
        expected_token = _confirmation_token(generation.id, generation.revision, dependencies)
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
                "GENERATION_DELETE_BLOCKED",
                "Active work or downstream references must be resolved "
                "before deleting this generation",
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
            resource_type="GENERATION",
            resource_id=generation.id,
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
