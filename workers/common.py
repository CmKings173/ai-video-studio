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

from apps.api.app.db.models import Asset, Scene, SceneGeneration, Video, utcnow
from apps.api.app.integrations.media import MediaValidationError, inspect_media

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
) -> str:
    """Persist intent before object I/O; repeat collection repairs partial writes."""
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
            if asset.checksum and asset.checksum != checksum:
                raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")
            recorded_ready = asset.status == "READY"
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
            session.add(asset)
    if recorded_ready:
        try:
            check_checksum(await store.get_bytes(object_key), checksum)
            return asset_id
        except Exception:
            async with factory() as session, session.begin():
                asset = await session.get(Asset, asset_id, with_for_update=True)
                if asset is None or (asset.checksum and asset.checksum != checksum):
                    raise ValueError("IMMUTABLE_OUTPUT_CONFLICT") from None
                asset.status = "PENDING_UPLOAD"
    await store.put_bytes(object_key, data, "video/mp4")
    # A read-after-write verifies bytes, not an S3 multipart ETag.
    check_checksum(await store.get_bytes(object_key), checksum)
    async with factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        asset.status = "READY"
        asset.width = metadata.get("width")
        asset.height = metadata.get("height")
        asset.duration_seconds = metadata.get("duration_seconds", metadata.get("duration"))
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


