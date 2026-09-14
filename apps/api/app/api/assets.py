from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import pagination
from apps.api.app.api.deps import idempotency_key, require_csrf, require_editor
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, User, utcnow
from apps.api.app.db.session import get_session
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import (
    AssetComplete,
    AssetDTO,
    DownloadDTO,
    Page,
    UploadDTO,
    UploadRequest,
)
from apps.api.app.services.asset_claims import claim_is_active, clear_claim
from apps.api.app.services.asset_service import (
    asset_is_referenced,
    complete_asset,
    create_pending_asset,
    upload_staging_key,
)
from apps.api.app.services.idempotency import claim
from apps.api.app.services.idempotency import complete as complete_idempotency

router = APIRouter(prefix="/assets", tags=["assets"])


def store(settings: Settings = Depends(get_settings)) -> AssetStore:
    return AssetStore(settings)


@router.get("", response_model=Page[AssetDTO])
async def list_assets(
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[AssetDTO]:
    page, size = paging
    where = Asset.deleted_at.is_(None)
    total = await session.scalar(select(func.count()).select_from(Asset).where(where)) or 0
    rows = list(
        (
            await session.scalars(
                select(Asset)
                .where(where)
                .order_by(Asset.created_at.desc(), Asset.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[AssetDTO.model_validate(row) for row in rows], total=total, page=page, page_size=size
    )


@router.post("/upload-url", response_model=UploadDTO, status_code=201)
async def upload_url(
    payload: UploadRequest,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    key: str = Depends(idempotency_key),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> UploadDTO:
    record, replay = await claim(
        session,
        user_id=user.id,
        operation="asset-upload-url",
        key=key,
        payload=payload.model_dump(mode="json"),
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        asset = await session.get(Asset, replay["asset_id"])
        if asset is None:
            raise AppError(
                "IDEMPOTENCY_RESOURCE_MISSING",
                "The original upload record is unavailable",
                409,
            )
        if asset.status == "READY":
            # A replay of an upload intent must never mint a new URL capable of
            # overwriting a content-addressed object that is already referenced.
            return UploadDTO(
                asset_id=asset.id,
                object_key=asset.object_key,
                upload={"completed": True},
            )
        if asset.status in {"DELETING", "DELETED"}:
            raise AppError("ASSET_STATE_CONFLICT", "Deleted assets cannot be uploaded", 409)
        staging_key = upload_staging_key(asset)
        upload = await asset_store.presign_upload(staging_key, asset.content_type)
        response = UploadDTO(asset_id=asset.id, object_key=staging_key, upload=upload)
        complete_idempotency(record, response.model_dump(mode="json"), 201)
        return response
    asset, upload = await create_pending_asset(session, asset_store, settings, payload, user.id)
    response = UploadDTO(
        asset_id=asset.id,
        object_key=upload_staging_key(asset),
        upload=upload,
    )
    complete_idempotency(record, response.model_dump(mode="json"), 201)
    return response


@router.post("/{asset_id}/complete", response_model=AssetDTO)
async def complete(
    asset_id: str,
    payload: AssetComplete,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> AssetDTO:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    asset = await complete_asset(session, asset_store, settings, asset, payload)
    return AssetDTO.model_validate(asset)


@router.get("/{asset_id}", response_model=AssetDTO)
async def get_asset(
    asset_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> AssetDTO:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.deleted_at is not None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    return AssetDTO.model_validate(asset)


@router.get("/{asset_id}/download", response_model=DownloadDTO)
async def download(
    asset_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    asset_store: AssetStore = Depends(store),
) -> DownloadDTO:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.status != "READY" or asset.deleted_at is not None:
        raise AppError("ASSET_NOT_READY", "Asset is missing or not ready", 404)
    return DownloadDTO(url=await asset_store.presign_download(asset.object_key, asset.filename))


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: str,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    asset = await session.get(Asset, asset_id, with_for_update=True)
    if asset is None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    if asset.status in {"DELETING", "DELETED"}:
        return
    claim_timeout_seconds = int(
        getattr(settings, "asset_operation_claim_timeout_seconds", 900)
    )
    if claim_is_active(asset, now=utcnow(), timeout_seconds=claim_timeout_seconds):
        raise AppError(
            "ASSET_OPERATION_BUSY",
            "Asset is currently owned by another operation",
            409,
        )
    if asset.operation_claim_id:
        clear_claim(asset)
    if await asset_is_referenced(session, asset_id):
        raise AppError("ASSET_IN_USE", "Referenced assets cannot be deleted", 409)
    asset.status = "DELETED"
    asset.deleted_at = utcnow()
