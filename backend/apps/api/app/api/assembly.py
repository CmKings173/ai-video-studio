from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.assets import store
from apps.api.app.api.deps import (
    AuthPrincipal,
    expected_revision,
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.api.ownership import (
    get_owned,
    get_video_output_asset,
    owner_scope,
    principal_id,
)
from apps.api.app.api.ownership import (
    is_admin as principal_is_admin,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import FinalVideo, Video, utcnow
from apps.api.app.db.session import get_session
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import AssemblyRequest, DownloadDTO, FinalDTO
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_freshness import restore_video_after_assembly
from apps.api.app.services.idempotency import claim, complete

router = APIRouter(tags=["assembly"])


@router.post("/videos/{video_id}/assemble", response_model=FinalDTO, status_code=202)
async def assemble_video(
    video_id: str,
    payload: AssemblyRequest,
    request: Request,
    revision: int = Depends(expected_revision),
    key: str = Depends(idempotency_key),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinalDTO:
    service = AssemblyService()
    is_admin = principal_is_admin(user)
    await service.authorize_inputs(
        session,
        video_id=video_id,
        request=payload,
        user_id=principal_id(user),
        is_admin=is_admin,
    )
    record, replay = await claim(
        session,
        user_id=principal_id(user),
        operation=f"assemble:{video_id}",
        key=key,
        payload={"revision": revision, **payload.model_dump(mode="json")},
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        final_id = replay.get("id")
        if not final_id:
            raise AppError(
                "IDEMPOTENCY_RESOURCE_MISSING",
                "The original final video is unavailable",
                409,
            )
        final = await get_owned(
            session,
            FinalVideo,
            final_id,
            user,
            error_code="FINAL_VIDEO_NOT_FOUND",
            message="Final video was not found",
        )
        if final.video_id != video_id:
            raise AppError("FINAL_VIDEO_NOT_FOUND", "Final video was not found", 404)
        return FinalDTO.model_validate(final)
    final = await service.create(
        session,
        video_id=video_id,
        request=payload,
        expected_revision=revision,
        user_id=principal_id(user),
        request_id=getattr(request.state, "request_id", None),
        is_admin=is_admin,
    )
    response = FinalDTO.model_validate(final)
    complete(record, response.model_dump(mode="json"), 202)
    return response


@router.get("/videos/{video_id}/final-versions", response_model=list[FinalDTO])
async def list_final_versions(
    video_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[FinalDTO]:
    video = await get_owned(
        session,
        Video,
        video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    rows = list(
        (
            await session.scalars(
                select(FinalVideo)
                .where(
                    FinalVideo.video_id == video.id,
                    owner_scope(FinalVideo, user),
                )
                .order_by(FinalVideo.version_no.desc(), FinalVideo.id.desc())
            )
        ).all()
    )
    return [FinalDTO.model_validate(row) for row in rows]


@router.get("/final-versions/{final_id}", response_model=FinalDTO)
async def get_final_version(
    final_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> FinalDTO:
    final = await get_owned(
        session,
        FinalVideo,
        final_id,
        user,
        error_code="FINAL_VIDEO_NOT_FOUND",
        message="Final video was not found",
    )
    return FinalDTO.model_validate(final)


@router.get("/final-versions/{final_id}/download", response_model=DownloadDTO)
async def download_final_version(
    final_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    asset_store: AssetStore = Depends(store),
) -> DownloadDTO:
    final = await get_owned(
        session,
        FinalVideo,
        final_id,
        user,
        error_code="FINAL_VIDEO_NOT_FOUND",
        message="Final video was not found",
    )
    if final is None or final.status != "READY" or not final.output_asset_id:
        raise AppError("FINAL_VIDEO_NOT_READY", "Final video is not ready", 409)
    video = await get_owned(
        session,
        Video,
        final.video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    asset = await get_video_output_asset(
        session,
        video,
        final.output_asset_id,
        user,
        role="FINAL_VIDEO",
        final_video_id=final.id,
    )
    if asset.status != "READY" or asset.deleted_at is not None:
        raise AppError("FINAL_VIDEO_NOT_READY", "Final video asset is unavailable", 409)
    return DownloadDTO(url=await asset_store.presign_download(asset.object_key, asset.filename))


@router.post("/final-versions/{final_id}/cancel", response_model=FinalDTO)
async def cancel_final_version(
    final_id: str,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> FinalDTO:
    final_snapshot = await get_owned(
        session,
        FinalVideo,
        final_id,
        user,
        error_code="FINAL_VIDEO_NOT_FOUND",
        message="Final video was not found",
    )
    video_id = final_snapshot.video_id
    video = await lock_revisioned_row(session, Video, video_id)
    final = await lock_revisioned_row(session, FinalVideo, final_id)
    is_owned = await session.scalar(
        select(FinalVideo.id).where(
            FinalVideo.id == final_id,
            owner_scope(FinalVideo, user),
        )
    )
    if final is None or video is None or final.video_id != video_id or is_owned is None:
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
        await restore_video_after_assembly(session, video, final.manifest, "SCENES_READY")
    else:
        final.status = "CANCEL_REQUESTED"
        final.phase = "CANCELLING"
    return FinalDTO.model_validate(final)
