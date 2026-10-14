from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import pagination
from apps.api.app.api.deps import AuthPrincipal, idempotency_key, require_csrf, require_editor
from apps.api.app.api.ownership import (
    get_owned,
    owner_scope,
    principal_id,
)
from apps.api.app.api.ownership import (
    is_admin as principal_is_admin,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Product, Project, utcnow
from apps.api.app.db.session import get_session
from apps.api.app.integrations.media import ALLOWED_TYPES
from apps.api.app.integrations.minio import AssetStore
from apps.api.app.schemas.api import (
    AssetComplete,
    AssetDeletionStatusDTO,
    AssetDTO,
    DownloadDTO,
    Page,
    UploadDTO,
    UploadRequest,
)
from apps.api.app.services.asset_claims import claim_is_active, clear_claim
from apps.api.app.services.asset_service import (
    asset_is_referenced,
    create_pending_asset,
    enqueue_asset_validation,
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
    project_id: str | None = Query(default=None),
    product_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[AssetDTO]:
    page, size = paging
    where = and_(Asset.deleted_at.is_(None), owner_scope(Asset, user))
    if project_id is not None and product_id is not None:
        where = and_(where, or_(Asset.project_id == project_id, Asset.product_id == product_id))
    elif project_id is not None:
        where = and_(where, Asset.project_id == project_id)
    elif product_id is not None:
        where = and_(where, Asset.product_id == product_id)
    if status is not None:
        where = and_(where, Asset.status == status)
    if kind is not None:
        where = and_(where, Asset.kind == kind)
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
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    key: str = Depends(idempotency_key),
    settings: Settings = Depends(get_settings),
    asset_store: AssetStore = Depends(store),
) -> UploadDTO:
    if payload.project_id is not None:
        project = await get_owned(
            session,
            Project,
            str(payload.project_id),
            user,
            error_code="PROJECT_NOT_FOUND",
            message="Project not found",
        )
        requested_scope = ("project_id", project.id)
    else:
        product = await get_owned(
            session,
            Product,
            str(payload.product_id),
            user,
            error_code="PRODUCT_NOT_FOUND",
            message="Product not found",
        )
        requested_scope = ("product_id", product.id)

    record, replay = await claim(
        session,
        user_id=principal_id(user),
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
        asset = await get_owned(
            session,
            Asset,
            replay["asset_id"],
            user,
            error_code="ASSET_NOT_FOUND",
            message="Asset not found",
        )
        if getattr(asset, requested_scope[0]) != requested_scope[1]:
            raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
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
    asset, upload = await create_pending_asset(
        session,
        asset_store,
        settings,
        payload,
        principal_id(user),
        is_admin=principal_is_admin(user),
    )
    response = UploadDTO(
        asset_id=asset.id,
        object_key=upload_staging_key(asset),
        upload=upload,
    )
    complete_idempotency(record, response.model_dump(mode="json"), 201)
    return response


@router.get("/upload-policy")
async def upload_policy(
    user: AuthPrincipal = Depends(require_editor),
    settings: Settings = Depends(get_settings),
) -> dict:
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "allowed_content_types": sorted(ALLOWED_TYPES),
    }


@router.post("/{asset_id}/complete", response_model=AssetDTO, status_code=202)
async def complete(
    asset_id: str,
    payload: AssetComplete,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AssetDTO:
    asset = await get_owned(
        session,
        Asset,
        asset_id,
        user,
        error_code="ASSET_NOT_FOUND",
        message="Asset not found",
    )
    asset = await enqueue_asset_validation(
        session,
        settings,
        asset,
        payload,
        user_id=principal_id(user),
        is_admin=principal_is_admin(user),
    )
    # Accepted means published: request dependency cleanup may run after send.
    await session.commit()
    return AssetDTO.model_validate(asset)


@router.get("/{asset_id}", response_model=AssetDTO)
async def get_asset(
    asset_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> AssetDTO:
    asset = await get_owned(
        session,
        Asset,
        asset_id,
        user,
        error_code="ASSET_NOT_FOUND",
        message="Asset not found",
    )
    if asset.deleted_at is not None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    return AssetDTO.model_validate(asset)


@router.get("/{asset_id}/download", response_model=DownloadDTO)
async def download(
    asset_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
    asset_store: AssetStore = Depends(store),
) -> DownloadDTO:
    asset = await get_owned(
        session,
        Asset,
        asset_id,
        user,
        error_code="ASSET_NOT_FOUND",
        message="Asset not found",
    )
    if asset.deleted_at is not None or asset.status in {"DELETING", "DELETED"}:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    if asset.status != "READY":
        raise AppError("ASSET_NOT_READY", "Asset is not ready", 409)
    return DownloadDTO(url=await asset_store.presign_download(asset.object_key, asset.filename))


@router.delete("/{asset_id}", status_code=202, response_model=AssetDeletionStatusDTO)
async def delete_asset(
    asset_id: str,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AssetDeletionStatusDTO:
    asset = await session.scalar(
        select(Asset)
        .where(Asset.id == asset_id, owner_scope(Asset, user))
        .with_for_update()
    )
    if asset is None:
        raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
    if asset.status in {"DELETING", "DELETED"}:
        return _asset_deletion_status(asset)
    claim_timeout_seconds = int(getattr(settings, "asset_operation_claim_timeout_seconds", 900))
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
    return _asset_deletion_status(asset)


@router.get("/{asset_id}/deletion-status", response_model=AssetDeletionStatusDTO)
async def asset_deletion_status(
    asset_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> AssetDeletionStatusDTO:
    asset = await get_owned(
        session,
        Asset,
        asset_id,
        user,
        error_code="ASSET_NOT_FOUND",
        message="Asset not found",
    )
    return _asset_deletion_status(asset)


def _asset_deletion_status(asset: Asset) -> AssetDeletionStatusDTO:
    is_deleted = asset.status in {"DELETING", "DELETED"} or asset.deleted_at is not None
    cleanup_status = (
        "COMPLETE"
        if asset.purged_at is not None
        else "PENDING"
        if is_deleted
        else "NOT_REQUESTED"
    )
    status = (
        ("DELETING" if asset.status == "DELETING" else "DELETED")
        if is_deleted
        else "NOT_REQUESTED"
    )
    return AssetDeletionStatusDTO(
        asset_id=asset.id,
        status=status,
        cleanup_status=cleanup_status,
        deleted_at=asset.deleted_at,
        purged_at=asset.purged_at,
    )
