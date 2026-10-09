import asyncio

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.db.session import get_session
from apps.api.app.integrations.comfy_adapter import ComfyAdapter
from apps.api.app.integrations.minio import AssetStore

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    postgres = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        postgres = False
    store = AssetStore(settings)
    adapter = ComfyAdapter(settings)
    minio, comfyui = await asyncio.gather(store.health(), adapter.health())
    healthy = postgres and minio and comfyui
    if not healthy:
        response.status_code = 503
    return {
        "status": "ok" if healthy else "degraded",
        "postgres": postgres,
        "minio": minio,
        "comfyui": comfyui,
    }
