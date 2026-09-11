from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.assets import store
from apps.api.app.api.deps import (
    expected_revision,
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, FinalVideo, User, Video, utcnow
from apps.api.app.db.session import get_session
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import AssemblyRequest, DownloadDTO, FinalDTO
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.idempotency import claim, complete

router = APIRouter(tags=["assembly"])


@router.post("/videos/{video_id}/assemble", response_model=FinalDTO, status_code=202)
async def assemble_video(
    video_id: str,
    payload: AssemblyRequest,
    request: Request,
    revision: int = Depends(expected_revision),
    key: str = Depends(idempotency_key),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinalDTO:
    record, replay = await claim(
        session,
        user_id=user.id,
        operation=f"assemble:{video_id}",
        key=key,
        payload={"revision": revision, **payload.model_dump(mode="json")},
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return FinalDTO.model_validate(replay)
    final = await AssemblyService().create(
        session,
        video_id=video_id,
        request=payload,
        expected_revision=revision,
        user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
    )
    response = FinalDTO.model_validate(final)
    complete(record, response.model_dump(mode="json"), 202)
    return response


@router.get("/videos/{video_id}/final-versions", response_model=list[FinalDTO])
async def list_final_versions(
    video_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[FinalDTO]:
    if await session.get(Video, video_id) is None:
        raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
    rows = list(
        (
            await session.scalars(
                select(FinalVideo)
                .where(FinalVideo.video_id == video_id)
                .order_by(FinalVideo.version_no.desc(), FinalVideo.id.desc())
            )
        ).all()
    )
    return [FinalDTO.model_validate(row) for row in rows]


@router.get("/final-versions/{final_id}", response_model=FinalDTO)
async def get_final_version(
    final_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> FinalDTO:
    final = await session.get(FinalVideo, final_id)
    if final is None:
        raise AppError("FINAL_VIDEO_NOT_FOUND", "Final video was not found", 404)
    return FinalDTO.model_validate(final)


@router.get("/final-versions/{final_id}/download", response_model=DownloadDTO)
async def download_final_version(
    final_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    asset_store: AssetStore = Depends(store),
) -> DownloadDTO:
    final = await session.get(FinalVideo, final_id)
    if final is None or final.status != "READY" or not final.output_asset_id:
        raise AppError("FINAL_VIDEO_NOT_READY", "Final video is not ready", 409)
    asset = await session.get(Asset, final.output_asset_id)
    if asset is None or asset.status != "READY" or asset.deleted_at is not None:
        raise AppError("FINAL_VIDEO_NOT_READY", "Final video asset is unavailable", 409)
    return DownloadDTO(url=await asset_store.presign_download(asset.object_key, asset.filename))


@router.post("/final-versions/{final_id}/cancel", response_model=FinalDTO)
async def cancel_final_version(
    final_id: str,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> FinalDTO:
    final_ref = await session.get(FinalVideo, final_id)
    if final_ref is None:
        raise AppError("FINAL_VIDEO_NOT_FOUND", "Final video was not found", 404)
    video = await session.get(Video, final_ref.video_id, with_for_update=True)
    final = await session.scalar(
        select(FinalVideo).where(FinalVideo.id == final_id).with_for_update()
    )
    if final is None or video is None:
        raise AppError("FINAL_VIDEO_NOT_FOUND", "Final video was not found", 404)
    if final.status in {"READY", "FAILED", "CANCELLED"}:
        return FinalDTO.model_validate(final)
    final.revision += 1
    final.progress_updated_at = utcnow()
    if final.status == "QUEUED":
        final.status = "CANCELLED"
        final.phase = "CANCELLED"
        final.finished_at = utcnow()
        final.claimed_by = None
        final.lease_expires_at = None
        video.status = "READY" if video.current_final_video_id else "SCENES_READY"
    else:
        final.status = "CANCEL_REQUESTED"
        final.phase = "CANCELLING"
    return FinalDTO.model_validate(final)
