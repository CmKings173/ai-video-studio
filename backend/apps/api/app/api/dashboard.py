from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.admin import system_status
from apps.api.app.api.assets import store
from apps.api.app.api.deps import require_editor
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.db.models import Asset, FinalVideo, Project, SceneGeneration, User, Video
from apps.api.app.db.session import get_session
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import DashboardSummaryDTO, SystemStatusDTO

router = APIRouter(tags=["dashboard"])


async def _count(session: AsyncSession, model, *conditions) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


@router.get("/dashboard/summary", response_model=DashboardSummaryDTO)
async def dashboard_summary(
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> DashboardSummaryDTO:
    return DashboardSummaryDTO(
        projects=await _count(session, Project, Project.archived.is_(False)),
        videos=await _count(session, Video),
        assets_ready=await _count(session, Asset, Asset.status == "READY"),
        generations_pending=await _count(
            session, SceneGeneration, SceneGeneration.status.in_({"CREATED", "QUEUED"})
        ),
        generations_running=await _count(
            session,
            SceneGeneration,
            SceneGeneration.status.in_({"DISPATCHING", "RUNNING", "COLLECTING"}),
        ),
        generations_failed=await _count(
            session, SceneGeneration, SceneGeneration.status == "FAILED"
        ),
        assemblies_pending=await _count(
            session, FinalVideo, FinalVideo.status.in_({"QUEUED", "ASSEMBLING"})
        ),
    )


@router.get("/system/status", response_model=SystemStatusDTO)
async def editor_system_status(
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> SystemStatusDTO:
    return await system_status(session, settings, asset_store)
