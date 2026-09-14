"""Small shared worker primitives; no external I/O while a DB lock is held."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from apps.api.app.db.models import Asset, Scene, SceneGeneration, Video, utcnow
from apps.api.app.integrations.media import MediaValidationError, inspect_media
from apps.api.app.integrations.minio import (
    AssetObjectMissingError,
    AssetStoreError,
    AssetStoreUnavailableError,
)
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    acquire_claim,
    claim_heartbeat,
    clear_claim,
    deletion_lifecycle_owns_object,
    owns_claim,
    release_claim,
    requeue_deletion_retry,
)

logger = logging.getLogger(__name__)
GENERATION_TERMINAL = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
GENERATION_ACTIVE = frozenset(
    {"DISPATCHING", "QUEUED", "RUNNING", "COLLECTING", "CANCEL_REQUESTED"}
)


def worker_id(kind: str) -> str:
    return f"{kind}-{uuid4()}"


def lease_deadline(settings):
    return utcnow() + timedelta(seconds=max(15, settings.lease_seconds))


async def lock_scheduler(session, key: int) -> None:
    """Serialize admission, including the empty-queue case, across processes."""
    if session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def refresh_video(session, video_id: str) -> None:
    """Project execution state without invalidating an editor's dirty revision."""
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None or video.status in {"DIRTY", "ARCHIVED", "ASSEMBLING"}:
        return
    scenes = list(
        (
            await session.scalars(
                select(Scene).where(Scene.video_id == video_id, Scene.enabled.is_(True))
            )
        ).all()
    )
    active = await session.scalar(
        select(SceneGeneration.id)
        .where(
            SceneGeneration.video_id == video_id,
            SceneGeneration.status.in_(GENERATION_ACTIVE | {"CREATED"}),
        )
        .limit(1)
    )
    if active:
        video.status = "GENERATING"
    elif scenes and all(scene.selected_generation_id for scene in scenes):
        video.status = "READY" if getattr(video, "kind", None) == "QUICK_CLIP" else "SCENES_READY"
    else:
        video.status = "STORYBOARD_READY" if scenes else "DRAFT"


@asynccontextmanager
async def staging_directory(settings, prefix: str):
    def prepare_root() -> Path:
        value = Path(settings.workspace_root).resolve() / "worker-staging"
        value.mkdir(parents=True, exist_ok=True)
        return value

    root = await asyncio.to_thread(prepare_root)
    usage = await asyncio.to_thread(shutil.disk_usage, root)
    if usage.free < settings.min_free_disk_bytes:
        raise RuntimeError("INSUFFICIENT_DISK_SPACE")
    # Only this context owns/removes the exact temporary child it creates.
    with TemporaryDirectory(prefix=prefix, dir=root) as directory:
        yield Path(directory)


def check_checksum(data: bytes, checksum: str | None) -> str:
    actual = hashlib.sha256(data).hexdigest()
    if checksum and actual != checksum:
        raise ValueError("ASSET_CHECKSUM_MISMATCH")
    return actual


async def validate_generated_video(
    data: bytes,
    filename: str,
    ffprobe_binary: str = "ffprobe",
    *,
    comfy_kind: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Validate an external generation result before it becomes a VIDEO asset."""
    if comfy_kind is not None and comfy_kind != "videos":
        raise MediaValidationError("output kind is not videos")
    inspected = metadata or await inspect_media(data, "video/mp4", filename, ffprobe_binary)
    if inspected.get("kind") not in (None, "VIDEO") or not inspected.get("has_video"):
        raise MediaValidationError("generated output has no video stream")
    return inspected


async def _release_output_claim(factory, asset_id: str, claim_id: str) -> None:
    async with factory() as session, session.begin():
        await release_claim(session, asset_id, claim_id, OUTPUT_WRITE_CLAIM)


def _validate_output_identity(
    asset: Asset,
    *,
    checksum: str,
    object_key: str,
    role: str,
    project_id: str | None,
    created_by: str,
    size: int,
) -> None:
    if (
        asset.checksum != checksum
        or asset.object_key != object_key
        or asset.role != role
        or asset.kind != "VIDEO"
        or asset.content_type != "video/mp4"
        or asset.project_id != project_id
        or asset.created_by != created_by
        or asset.size_bytes != size
    ):
        raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")


async def _compensate_lost_output(
    factory,
    store,
    *,
    asset_id: str,
    object_key: str,
    checksum: str,
    size: int,
    claim_id: str,
) -> None:
    """Remove our bytes when retention owns deletion of the canonical key."""
    current_status = "MISSING"
    safe_delete = False
    retry_queued = False
    needs_retry_before_delete = False
    async with factory() as session:
        asset = await session.get(Asset, asset_id)
        if asset:
            current_status = asset.status
            safe_delete = deletion_lifecycle_owns_object(asset, object_key)
            needs_retry_before_delete = asset.status == "DELETED" and asset.purged_at is not None
    if safe_delete and needs_retry_before_delete:
        try:
            retry_queued = await requeue_deletion_retry(factory, asset_id, object_key)
        except SQLAlchemyError as exc:
            safe_delete = False
            error_type = type(exc).__name__
        else:
            if not retry_queued:
                safe_delete = False
                error_type = "RETRY_REQUEUE_FAILED"
    if safe_delete:
        try:
            await store.delete(object_key)
            return
        except (AssetStoreError, OSError, TimeoutError, ConnectionError) as exc:
            error_type = type(exc).__name__
            try:
                retry_queued = retry_queued or await requeue_deletion_retry(
                    factory, asset_id, object_key
                )
            except SQLAlchemyError as retry_exc:
                error_type = f"{error_type};{type(retry_exc).__name__}"
    else:
        error_type = locals().get("error_type", "UNSAFE_TO_DELETE")
    logger.error(
        "asset_output_compensation_unresolved",
        extra={
            "asset_id": asset_id,
            "object_key": object_key,
            "claim_id": claim_id,
            "claim_type": OUTPUT_WRITE_CLAIM,
            "operation": "put_compensation",
            "current_status": current_status,
            "error_type": error_type,
            "retry_queued": retry_queued,
            "checksum": checksum,
            "size_bytes": size,
        },
    )


async def save_output(
    factory,
    store,
    *,
    owner_id: str,
    role: str,
    project_id: str | None,
    created_by: str,
    data: bytes,
    metadata: dict,
    claim_timeout_seconds: int = 900,
) -> str:
    """Persist output intent and own the asset through all canonical object I/O."""
    if role in {"GENERATED_VIDEO", "FINAL_VIDEO"} and (
        metadata.get("kind") not in (None, "VIDEO") or not metadata.get("has_video")
    ):
        raise ValueError("OUTPUT_NOT_VIDEO")
    checksum = check_checksum(data, None)
    asset_id = str(uuid5(NAMESPACE_URL, f"ai-video-studio:{role}:{owner_id}"))
    object_key = f"outputs/{role.lower()}/{owner_id}/{checksum}.mp4"
    recorded_ready = False
    async with factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        if asset is not None:
            _validate_output_identity(
                asset,
                checksum=checksum,
                object_key=object_key,
                role=role,
                project_id=project_id,
                created_by=created_by,
                size=len(data),
            )
            if asset.status not in {"PENDING_UPLOAD", "READY"}:
                raise ValueError("ASSET_STATE_CONFLICT")
        else:
            asset = Asset(
                id=asset_id,
                project_id=project_id,
                kind="VIDEO",
                role=role,
                filename=f"{owner_id}.mp4",
                content_type="video/mp4",
                object_key=object_key,
                status="PENDING_UPLOAD",
                checksum=checksum,
                size_bytes=len(data),
                created_by=created_by,
            )
            try:
                async with session.begin_nested():
                    session.add(asset)
                    await session.flush()
            except IntegrityError:
                asset = await session.get(Asset, asset_id, with_for_update=True)
                if asset is None:
                    raise
            _validate_output_identity(
                asset,
                checksum=checksum,
                object_key=object_key,
                role=role,
                project_id=project_id,
                created_by=created_by,
                size=len(data),
            )
        recorded_ready = asset.status == "READY"
        claim_id = await acquire_claim(
            session,
            asset_id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=claim_timeout_seconds,
            allowed_statuses={"PENDING_UPLOAD", "READY"},
        )
        if claim_id is None:
            raise ValueError("ASSET_OPERATION_BUSY")

    if recorded_ready:
        try:
            check_checksum(await store.get_bytes(object_key), checksum)
        except (AssetObjectMissingError, KeyError, FileNotFoundError):
            immutable_conflict = False
            async with factory() as session, session.begin():
                asset = await session.get(Asset, asset_id, with_for_update=True)
                if not asset or not owns_claim(asset, claim_id, OUTPUT_WRITE_CLAIM):
                    raise ValueError("ASSET_OPERATION_BUSY") from None
                if asset.checksum and asset.checksum != checksum:
                    clear_claim(asset)
                    immutable_conflict = True
                elif asset.status == "READY":
                    asset.status = "PENDING_UPLOAD"
                    asset.failed_at = None
            if immutable_conflict:
                raise ValueError("IMMUTABLE_OUTPUT_CONFLICT") from None
        except (
            AssetStoreUnavailableError,
            AssetStoreError,
            TimeoutError,
            ConnectionError,
            OSError,
        ):
            await _release_output_claim(factory, asset_id, claim_id)
            raise
        else:
            state_conflict = False
            async with factory() as session, session.begin():
                asset = await session.get(Asset, asset_id, with_for_update=True)
                if not asset or not owns_claim(asset, claim_id, OUTPUT_WRITE_CLAIM):
                    raise ValueError("ASSET_OPERATION_BUSY")
                if asset.status != "READY":
                    clear_claim(asset)
                    state_conflict = True
                else:
                    clear_claim(asset)
            if state_conflict:
                raise ValueError("ASSET_STATE_CONFLICT")
            return asset_id

    wrote_object = False
    try:
        async with claim_heartbeat(
            factory,
            asset_id,
            claim_id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=claim_timeout_seconds,
            allowed_statuses={"PENDING_UPLOAD", "READY"},
        ) as lost:
            await store.put_bytes(object_key, data, "video/mp4")
            wrote_object = True
            # A read-after-write verifies bytes, not an S3 multipart ETag.
            check_checksum(await store.get_bytes(object_key), checksum)
            if lost.is_set():
                raise ValueError("ASSET_OPERATION_BUSY")
    except (
        AssetStoreUnavailableError,
        AssetStoreError,
        TimeoutError,
        ConnectionError,
        OSError,
        ValueError,
    ):
        await _release_output_claim(factory, asset_id, claim_id)
        if wrote_object:
            await _compensate_lost_output(
                factory,
                store,
                asset_id=asset_id,
                object_key=object_key,
                checksum=checksum,
                size=len(data),
                claim_id=claim_id,
            )
        raise

    lost_ownership = False
    state_conflict = False
    async with factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        if not asset:
            raise ValueError("ASSET_NOT_FOUND")
        if not owns_claim(asset, claim_id, OUTPUT_WRITE_CLAIM):
            lost_ownership = True
        elif asset.status != "PENDING_UPLOAD":
            clear_claim(asset)
            state_conflict = True
        else:
            asset.status = "READY"
            asset.failed_at = None
            asset.width = metadata.get("width")
            asset.height = metadata.get("height")
            asset.duration_seconds = metadata.get("duration_seconds", metadata.get("duration"))
            clear_claim(asset)
    if lost_ownership:
        await _compensate_lost_output(
            factory,
            store,
            asset_id=asset_id,
            object_key=object_key,
            checksum=checksum,
            size=len(data),
            claim_id=claim_id,
        )
        raise ValueError("ASSET_OPERATION_BUSY")
    if state_conflict:
        await _compensate_lost_output(
            factory,
            store,
            asset_id=asset_id,
            object_key=object_key,
            checksum=checksum,
            size=len(data),
            claim_id=claim_id,
        )
        raise ValueError("ASSET_STATE_CONFLICT")
    return asset_id


async def service_loop(worker, poll_seconds: float) -> None:
    while True:
        try:
            worked = await worker.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("worker_iteration_failed")
            worked = False
        if not worked:
            await asyncio.sleep(max(0.1, poll_seconds))
