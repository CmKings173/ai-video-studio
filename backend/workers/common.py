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

from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    Scene,
    SceneGeneration,
    Video,
    utcnow,
)
from apps.api.app.integrations.media import MediaValidationError, inspect_media, inspect_media_path
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
from apps.api.app.services.generation_freshness import (
    is_assembly_source_current,
    is_selected_generation_fresh,
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


async def oldest_pending_generation(session):
    """Lock both queue heads under the admission lock and compare admissible age.

    Routing outside this transaction is only a hint. Row locks fence cancellation
    and SKIP LOCKED lets work held by an editor yield to the other queue.
    """
    standalone = (
        ~select(DirectorRunMember.id)
        .where(DirectorRunMember.scene_generation_id == SceneGeneration.id)
        .exists()
    )
    heads = []
    for kind, model in (("director", DirectorRun), ("standalone", SceneGeneration)):
        query = select(model).where(model.status == "CREATED")
        if kind == "standalone":
            query = query.where(standalone)
        row = await session.scalar(
            query.order_by(model.created_at, model.id).with_for_update(skip_locked=True).limit(1)
        )
        if row is not None:
            heads.append((kind, row))
    return (
        min(heads, key=lambda item: (item[1].created_at, item[1].id, item[0]))
        if heads
        else (None, None)
    )


async def refresh_video(session, video_id: str) -> None:
    """Project execution state without invalidating an editor's dirty revision."""
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None or video.status in {"DIRTY", "ARCHIVED", "ASSEMBLING"}:
        return
    # Sessions disable autoflush: readiness queries must see this completion,
    # rather than the job's previous RUNNING row in the database.
    await session.flush()
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
        fresh = [await is_selected_generation_fresh(session, scene, video) for scene in scenes]
        final = (
            await session.get(FinalVideo, video.current_final_video_id)
            if video.current_final_video_id
            else None
        )
        if (
            all(fresh)
            and final
            and final.status == "READY"
            and await is_assembly_source_current(session, video, final.manifest)
        ):
            video.status = "READY"
            return
        video.status = (
            ("READY" if getattr(video, "kind", None) == "QUICK_CLIP" else "SCENES_READY")
            if all(fresh)
            else "DIRTY"
        )
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
    if usage.free < getattr(settings, "min_free_disk_bytes", 0):
        raise RuntimeError("INSUFFICIENT_DISK_SPACE")
    # Only this context owns/removes the exact temporary child it creates.
    with TemporaryDirectory(prefix=prefix, dir=root) as directory:
        yield Path(directory)


async def require_staging_space(directory: Path, required_bytes: int, reserve_bytes: int) -> None:
    """Keep active worker staging and a configured free-space reserve available."""
    if required_bytes < 0 or reserve_bytes < 0:
        raise ValueError("Invalid staging capacity requirement")
    usage = await asyncio.to_thread(shutil.disk_usage, directory)
    if usage.free < required_bytes + reserve_bytes:
        raise RuntimeError("INSUFFICIENT_DISK_SPACE")


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


async def validate_generated_video_path(
    path: Path,
    ffprobe_binary: str = "ffprobe",
    *,
    comfy_kind: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Apply the same video contract to a bounded worker-owned file."""
    if comfy_kind is not None and comfy_kind != "videos":
        raise MediaValidationError("output kind is not videos")
    inspected = metadata or await inspect_media_path(path, "video/mp4", ffprobe_binary)
    if inspected.get("kind") not in (None, "VIDEO") or not inspected.get("has_video"):
        raise MediaValidationError("generated output has no video stream")
    return {**inspected, "kind": "VIDEO"}


async def _await_owned_task(task):
    """A cancelled caller must wait for its file reader to relinquish the path."""
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        # Observe any thread exception, but preserve the caller's cancellation.
        if not task.cancelled():
            task.exception()
        raise


def checksum_file(path: Path, max_bytes: int) -> dict:
    """Hash actual file bytes with both an early and runtime size bound."""
    expected_size = path.stat().st_size
    if expected_size <= 0 or expected_size > max_bytes:
        raise ValueError("OUTPUT_TOO_LARGE")
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("OUTPUT_TOO_LARGE")
            digest.update(chunk)
    if size != expected_size:
        raise ValueError("OUTPUT_FILE_CHANGED")
    return {"checksum": digest.hexdigest(), "size": size}


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
    max_bytes: int = 500 * 1024**2,
) -> str:
    """Compatibility API for small injected/test payloads; workers use files."""
    if not data or len(data) > max_bytes:
        raise ValueError("OUTPUT_TOO_LARGE")

    async def upload(key):
        await store.put_bytes(key, data, "video/mp4")

    return await _save_output(
        factory,
        store,
        owner_id=owner_id,
        role=role,
        project_id=project_id,
        created_by=created_by,
        checksum=check_checksum(data, None),
        size=len(data),
        upload=upload,
        metadata=metadata,
        claim_timeout_seconds=claim_timeout_seconds,
        max_bytes=max_bytes,
    )


async def save_output_file(
    factory,
    store,
    *,
    owner_id: str,
    role: str,
    project_id: str | None,
    created_by: str,
    path: Path,
    metadata: dict,
    claim_timeout_seconds: int = 900,
    max_bytes: int = 500 * 1024**2,
    expected_checksum: str | None = None,
    expected_size: int | None = None,
) -> str:
    """Canonical production output: file digest, immutable PUT, verify, READY."""
    actual = await _await_owned_task(
        asyncio.create_task(asyncio.to_thread(checksum_file, path, max_bytes))
    )
    if (expected_checksum is not None and actual["checksum"] != expected_checksum) or (
        expected_size is not None and actual["size"] != expected_size
    ):
        raise ValueError("OUTPUT_FILE_CHANGED")
    metadata = await validate_generated_video_path(path, metadata=metadata)

    async def upload(key):
        await store.put_file_immutable(key, path, "video/mp4", actual["checksum"], max_bytes)

    return await _save_output(
        factory,
        store,
        owner_id=owner_id,
        role=role,
        project_id=project_id,
        created_by=created_by,
        checksum=actual["checksum"],
        size=actual["size"],
        upload=upload,
        metadata=metadata,
        claim_timeout_seconds=claim_timeout_seconds,
        max_bytes=max_bytes,
    )


async def _save_output(
    factory,
    store,
    *,
    owner_id: str,
    role: str,
    project_id: str | None,
    created_by: str,
    checksum: str,
    size: int,
    upload,
    metadata: dict,
    claim_timeout_seconds: int = 900,
    max_bytes: int = 500 * 1024**2,
) -> str:
    """Persist output intent and own the asset through all canonical object I/O."""
    if size <= 0 or size > max_bytes:
        raise ValueError("OUTPUT_TOO_LARGE")
    if role in {"GENERATED_VIDEO", "FINAL_VIDEO"} and (
        metadata.get("kind") not in (None, "VIDEO") or not metadata.get("has_video")
    ):
        raise ValueError("OUTPUT_NOT_VIDEO")

    async def verify_stored(key):
        if hasattr(store, "checksum_object"):
            actual = await store.checksum_object(key, max_bytes)
            if actual["checksum"] != checksum or actual["size"] != size:
                raise ValueError("ASSET_CHECKSUM_MISMATCH")
        else:
            # Compatibility for injected stores; production AssetStore streams.
            check_checksum(await store.get_bytes(key), checksum)

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
                size=size,
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
                size_bytes=size,
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
                size=size,
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

    wrote_object = False

    async def cleanup():
        try:
            await _release_output_claim(factory, asset_id, claim_id)
        finally:
            if wrote_object:
                await _compensate_lost_output(
                    factory,
                    store,
                    asset_id=asset_id,
                    object_key=object_key,
                    checksum=checksum,
                    size=size,
                    claim_id=claim_id,
                )

    try:
        if recorded_ready:
            try:
                async with claim_heartbeat(
                    factory,
                    asset_id,
                    claim_id,
                    OUTPUT_WRITE_CLAIM,
                    timeout_seconds=claim_timeout_seconds,
                    allowed_statuses={"PENDING_UPLOAD", "READY"},
                ) as lost:
                    await verify_stored(object_key)
                    if lost.is_set():
                        raise ValueError("ASSET_OPERATION_BUSY")
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
                ValueError,
                asyncio.CancelledError,
            ):
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

        try:
            async with claim_heartbeat(
                factory,
                asset_id,
                claim_id,
                OUTPUT_WRITE_CLAIM,
                timeout_seconds=claim_timeout_seconds,
                allowed_statuses={"PENDING_UPLOAD", "READY"},
            ) as lost:
                async with factory() as session:
                    current = await session.get(Asset, asset_id)
                    if (
                        current is None
                        or not owns_claim(current, claim_id, OUTPUT_WRITE_CLAIM)
                        or current.status != "PENDING_UPLOAD"
                        or lost.is_set()
                    ):
                        raise ValueError("ASSET_OPERATION_BUSY")
                wrote_object = True  # a failed/cancelled PUT can still have reached storage
                await upload(object_key)
                # A read-after-write verifies bytes, not an S3 multipart ETag.
                await verify_stored(object_key)
                if lost.is_set():
                    raise ValueError("ASSET_OPERATION_BUSY")
        except (
            AssetStoreUnavailableError,
            AssetStoreError,
            TimeoutError,
            ConnectionError,
            OSError,
            ValueError,
            SQLAlchemyError,
            asyncio.CancelledError,
        ):
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
                asset.media_metadata = {
                    **metadata,
                    "checksum": checksum,
                    "size_bytes": size,
                    "inspection_method": metadata.get("inspection_method", "ffprobe_declarations"),
                }
                clear_claim(asset)
        if lost_ownership:
            raise ValueError("ASSET_OPERATION_BUSY")
        if state_conflict:
            raise ValueError("ASSET_STATE_CONFLICT")
        return asset_id
    except (Exception, asyncio.CancelledError):
        # Includes cancellation/DB failure between verified PUT and publication.
        # Storage adapters relinquish their file before this cleanup/context exit.
        try:
            await _await_owned_task(asyncio.create_task(cleanup()))
        except Exception:
            logger.exception("asset_output_cleanup_failed", extra={"asset_id": asset_id})
        raise


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
