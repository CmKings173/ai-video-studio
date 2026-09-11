from __future__ import annotations

import hmac
import shutil
from asyncio import to_thread
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.metrics import metrics
from apps.api.app.db.models import FinalVideo, SceneGeneration
from apps.api.app.db.session import get_session_factory
from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.minio import AssetStore

router = APIRouter(tags=["metrics"])


async def _refresh_database_gauges(session: AsyncSession, settings: Settings) -> None:
    generation_states = {
        "queued": {"CREATED", "QUEUED"},
        "running": {"DISPATCHING", "RUNNING", "COLLECTING"},
        "failed": {"FAILED"},
    }
    for name, statuses in generation_states.items():
        value = await session.scalar(
            select(func.count()).select_from(SceneGeneration).where(SceneGeneration.status.in_(statuses))
        )
        metrics.set(f"studio_generation_{name}_count", value or 0)
    oldest_generation = await session.scalar(
        select(func.min(SceneGeneration.created_at)).where(
            SceneGeneration.status.in_(generation_states["queued"])
        )
    )
    oldest_assembly = await session.scalar(
        select(func.min(FinalVideo.created_at)).where(FinalVideo.status == "QUEUED")
    )
    now = datetime.now(UTC)
    for name, timestamp in {
        "generation": oldest_generation,
        "assembly": oldest_assembly,
    }.items():
        if timestamp is None:
            metrics.set(f"studio_{name}_queue_oldest_age_seconds", 0)
        else:
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            metrics.set(
                f"studio_{name}_queue_oldest_age_seconds",
                max(0, (now - timestamp).total_seconds()),
            )
    for name, statuses in {
        "queued": {"QUEUED"},
        "running": {"ASSEMBLING"},
        "failed": {"FAILED"},
    }.items():
        value = await session.scalar(
            select(func.count()).select_from(FinalVideo).where(FinalVideo.status.in_(statuses))
        )
        metrics.set(f"studio_assembly_{name}_count", value or 0)
    stale_generation = await session.scalar(
        select(func.count()).select_from(SceneGeneration).where(
            SceneGeneration.lease_expires_at.is_not(None),
            SceneGeneration.lease_expires_at < func.now(),
            SceneGeneration.status.not_in({"COMPLETED", "FAILED", "CANCELLED"}),
        )
    )
    stale_assembly = await session.scalar(
        select(func.count()).select_from(FinalVideo).where(
            FinalVideo.lease_expires_at.is_not(None),
            FinalVideo.lease_expires_at < func.now(),
            FinalVideo.status.not_in({"READY", "FAILED", "CANCELLED"}),
        )
    )
    metrics.set("studio_generation_stale_lease_count", stale_generation or 0)
    metrics.set("studio_assembly_stale_lease_count", stale_assembly or 0)
    free_bytes = await to_thread(shutil.disk_usage, settings.workspace_root)
    metrics.set("studio_workspace_free_bytes", free_bytes.free)
    if settings.backup_status_file:
        try:
            modified = await to_thread(settings.backup_status_file.stat)
            metrics.set(
                "studio_backup_age_seconds",
                max(0, (now - datetime.fromtimestamp(modified.st_mtime, UTC)).total_seconds()),
            )
        except OSError:
            metrics.set("studio_backup_age_seconds", -1)
    else:
        metrics.set("studio_backup_age_seconds", -1)


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(
    request: Request,
    settings: Settings = Depends(get_settings),
    factory=Depends(get_session_factory),
) -> PlainTextResponse:
    supplied = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.metrics_token}" if settings.metrics_token else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise AppError("METRICS_UNAUTHORIZED", "Metrics authentication required", 401)
    async with factory() as session:
        await _refresh_database_gauges(session, settings)
    metrics.set("studio_postgres_up", 1)
    metrics.set("studio_minio_up", int(await AssetStore(settings).health()))
    metrics.set("studio_comfyui_up", int(await ComfyAdapter(settings).health()))
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
