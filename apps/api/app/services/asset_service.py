from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    FinalVideoScene,
    GenerationAsset,
    Product,
    Project,
    SceneGeneration,
    utcnow,
)
from apps.api.app.integrations.media import (
    MediaInspectionError,
    MediaValidationError,
    inspect_media,
    kind_for_content_type,
)
from apps.api.app.integrations.minio import (
    AssetObjectMissingError,
    AssetStore,
    AssetStoreError,
    AssetStoreUnavailableError,
)
from apps.api.app.schemas.api import AssetComplete, UploadRequest
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    acquire_claim,
    claim_heartbeat,
    claim_is_active,
    clear_claim,
    deletion_lifecycle_owns_object,
    owns_claim,
    release_claim,
    requeue_deletion_retry,
)

logger = logging.getLogger(__name__)


_COMPLETE_STATUSES = frozenset({"PENDING", "PENDING_UPLOAD", "VALIDATING", "FAILED"})


class _CompleteClaimLost(Exception):
    pass


class _PromotionFailed(Exception):
    pass


def immutable_asset_key(payload: UploadRequest, asset_id: str) -> str:
    owner = str(payload.project_id or payload.product_id)
    scope = "projects" if payload.project_id else "products"
    extension = Path(payload.filename).suffix.lower()[:12]
    return f"assets/{scope}/{owner}/{asset_id}/source{extension}"


def upload_staging_key(asset: Asset | None = None, *, asset_id: str | None = None) -> str:
    """Return the deterministic, mutable key used only during browser upload.

    The database always stores the immutable destination in ``Asset.object_key``;
    deriving the staging key keeps the recovery path durable without adding a
    second mutable column to the asset record.
    """
    resolved_id = asset_id or (asset.id if asset is not None else None)
    if not resolved_id:
        raise ValueError("asset_id is required")
    return f"staging/assets/{resolved_id}/upload"


async def create_pending_asset(
    session: AsyncSession,
    store: AssetStore,
    settings: Settings,
    payload: UploadRequest,
    user_id: str,
) -> tuple[Asset, dict]:
    if payload.size_bytes > settings.max_upload_bytes:
        raise AppError("UPLOAD_TOO_LARGE", "Upload exceeds configured size limit", 413)
    if payload.project_id:
        owner = await session.get(Project, str(payload.project_id))
        if owner is None or owner.archived:
            raise AppError("PROJECT_NOT_ACTIVE", "Project is missing or archived", 409)
    else:
        owner = await session.get(Product, str(payload.product_id))
        if owner is None or owner.archived:
            raise AppError("PRODUCT_NOT_ACTIVE", "Product is missing or archived", 409)
    asset_id = str(uuid4())
    asset = Asset(
        id=asset_id,
        project_id=str(payload.project_id) if payload.project_id else None,
        product_id=str(payload.product_id) if payload.product_id else None,
        kind=kind_for_content_type(payload.content_type),
        role=payload.role,
        filename=payload.filename,
        content_type=payload.content_type,
        object_key=immutable_asset_key(payload, asset_id),
        status="PENDING_UPLOAD",
        size_bytes=payload.size_bytes,
        created_by=user_id,
    )
    expected_kind = {
        "PRODUCT_IMAGE": "IMAGE",
        "REFERENCE_VIDEO": "VIDEO",
        "REFERENCE_AUDIO": "AUDIO",
        "BACKGROUND_AUDIO": "AUDIO",
    }.get(payload.role)
    if expected_kind and asset.kind != expected_kind:
        raise AppError(
            "ASSET_TYPE_INVALID",
            f"{payload.role} requires a {expected_kind.lower()} upload",
            422,
        )
    session.add(asset)
    await session.flush()
    upload = await store.presign_upload(upload_staging_key(asset), asset.content_type)
    return asset, upload


async def complete_asset(
    session: AsyncSession,
    store: AssetStore,
    settings: Settings,
    asset: Asset,
    payload: AssetComplete,
    *,
    factory=None,
) -> Asset:
    asset_id = asset.id
    claim_timeout_seconds = int(
        getattr(settings, "asset_operation_claim_timeout_seconds", 900)
    )
    session_factory = factory or async_sessionmaker(
        session.bind, expire_on_commit=False, autoflush=False
    )

    ready_asset: Asset | None = None
    ready_busy = False
    ready_checksum_conflict = False
    claim_id: str | None = None
    source_snapshot: dict | None = None
    async with session_factory() as phase, phase.begin():
        row = await phase.get(Asset, asset_id, with_for_update=True)
        if row is None:
            raise AppError("ASSET_NOT_FOUND", "Asset not found", 404)
        if row.status == "READY":
            if row.operation_claim_id:
                if claim_is_active(row, now=utcnow(), timeout_seconds=claim_timeout_seconds):
                    ready_busy = True
                else:
                    clear_claim(row)
            if (
                not ready_busy
                and payload.checksum_sha256
                and row.checksum
                and payload.checksum_sha256.lower() != row.checksum.lower()
            ):
                ready_checksum_conflict = True
            if not ready_busy and not ready_checksum_conflict:
                ready_asset = row
        elif row.status not in _COMPLETE_STATUSES:
            raise AppError(
                "ASSET_STATE_CONFLICT", "Asset cannot be completed in its current state", 409
            )
        else:
            claim_id = await acquire_claim(
                phase,
                asset_id,
                OUTPUT_WRITE_CLAIM,
                timeout_seconds=claim_timeout_seconds,
                allowed_statuses=_COMPLETE_STATUSES,
            )
            if claim_id is None:
                raise AppError(
                    "ASSET_OPERATION_BUSY",
                    "Asset is currently owned by another operation",
                    409,
                )
            row.status = "VALIDATING"
            row.failed_at = None
            source_snapshot = {
                "object_key": row.object_key,
                "content_type": row.content_type,
                "filename": row.filename,
                "size_bytes": row.size_bytes,
            }

    if ready_busy:
        raise AppError(
            "ASSET_OPERATION_BUSY",
            "Asset is currently owned by another operation",
            409,
        )
    if ready_checksum_conflict:
        raise AppError(
            "ASSET_CHECKSUM_CONFLICT",
            "Completed asset checksum does not match this request",
            409,
        )
    if ready_asset is not None:
        return ready_asset
    assert claim_id is not None and source_snapshot is not None

    async def release_complete_claim() -> None:
        async with session_factory() as phase, phase.begin():
            await release_claim(phase, asset_id, claim_id, OUTPUT_WRITE_CLAIM)

    async def fail_complete_claim() -> Asset | None:
        async with session_factory() as phase, phase.begin():
            failed = await phase.get(Asset, asset_id, with_for_update=True)
            if not failed or not owns_claim(failed, claim_id, OUTPUT_WRITE_CLAIM):
                return None
            failed.status = "FAILED"
            failed.failed_at = datetime.now(UTC)
            clear_claim(failed)
            return failed

    async def compensate_promoted_object(object_key: str, checksum: str, size: int) -> None:
        current_status = "MISSING"
        safe_delete = False
        retry_queued = False
        needs_retry_before_delete = False
        async with session_factory() as phase:
            current = await phase.get(Asset, asset_id)
            if current:
                current_status = current.status
                safe_delete = deletion_lifecycle_owns_object(current, object_key)
                needs_retry_before_delete = (
                    current.status == "DELETED" and current.purged_at is not None
                )
        if safe_delete and needs_retry_before_delete:
            try:
                retry_queued = await requeue_deletion_retry(
                    session_factory, asset_id, object_key
                )
            except SQLAlchemyError as exc:
                safe_delete = False
                error_type = type(exc).__name__
            else:
                if not retry_queued:
                    safe_delete = False
                    error_type = "RETRY_REQUEUE_FAILED"
        if not safe_delete:
            logger.warning(
                "asset_complete_compensation_unresolved",
                extra={
                    "asset_id": asset_id,
                    "object_key": object_key,
                    "claim_id": claim_id,
                    "claim_type": OUTPUT_WRITE_CLAIM,
                    "current_status": current_status,
                    "error_type": locals().get("error_type", "UNSAFE_TO_DELETE"),
                    "operation": "complete_promote",
                    "retry_queued": retry_queued,
                    "checksum": checksum,
                    "size_bytes": size,
                },
            )
            return
        try:
            await store.delete(object_key)
        except (AssetStoreError, OSError, TimeoutError, ConnectionError) as exc:
            try:
                retry_queued = retry_queued or await requeue_deletion_retry(
                    session_factory, asset_id, object_key
                )
            except SQLAlchemyError as retry_exc:
                retry_error = type(retry_exc).__name__
            else:
                retry_error = None
            logger.warning(
                "asset_complete_compensation_failed",
                extra={
                    "asset_id": asset_id,
                    "object_key": object_key,
                    "claim_id": claim_id,
                    "claim_type": OUTPUT_WRITE_CLAIM,
                    "error_type": type(exc).__name__,
                    "retry_error_type": retry_error,
                    "operation": "complete_promote",
                    "retry_queued": retry_queued,
                },
            )

    staging_key = upload_staging_key(asset_id=asset_id)
    canonical_key = source_snapshot["object_key"]
    content_type = source_snapshot["content_type"]
    filename = source_snapshot["filename"]
    expected_size = source_snapshot["size_bytes"]
    promoted_key: str | None = None
    try:
        async with claim_heartbeat(
            session_factory,
            asset_id,
            claim_id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=claim_timeout_seconds,
            allowed_statuses={"VALIDATING"},
        ) as lost:
            try:
                head = await store.head(staging_key)
                source_key = staging_key
            except (AssetObjectMissingError, KeyError, FileNotFoundError):
                # A reconciler may have promoted the object before this request
                # committed. The immutable key is a valid recovery source.
                try:
                    head = await store.head(canonical_key)
                except (AssetObjectMissingError, KeyError, FileNotFoundError) as exc:
                    raise MediaValidationError("uploaded object is missing") from exc
                except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
                    raise AppError(
                        "ASSET_STORE_UNAVAILABLE",
                        "Object storage is temporarily unavailable",
                        503,
                    ) from exc
                source_key = canonical_key
            except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
                raise AppError(
                    "ASSET_STORE_UNAVAILABLE",
                    "Object storage is temporarily unavailable",
                    503,
                ) from exc
            except AssetStoreError as exc:
                raise MediaValidationError(str(exc)) from exc
            if head["size"] <= 0 or head["size"] > settings.max_upload_bytes:
                raise MediaValidationError("uploaded object size is invalid")
            if head["size"] != expected_size:
                raise MediaValidationError("uploaded object size does not match request")
            if head["content_type"].split(";", 1)[0] != content_type:
                raise MediaValidationError("uploaded content type does not match request")
            try:
                data = await store.read(source_key, settings.max_upload_bytes)
            except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
                raise AppError(
                    "ASSET_STORE_UNAVAILABLE",
                    "Object storage is temporarily unavailable",
                    503,
                ) from exc
            except (AssetObjectMissingError, KeyError, FileNotFoundError) as exc:
                raise MediaValidationError("uploaded object is missing") from exc
            except AssetStoreError as exc:
                raise MediaValidationError(str(exc)) from exc
            checksum = hashlib.sha256(data).hexdigest()
            if payload.checksum_sha256 and checksum.lower() != payload.checksum_sha256.lower():
                raise MediaValidationError("uploaded checksum does not match")
            try:
                metadata = await inspect_media(
                    data, content_type, filename, settings.ffprobe_binary
                )
            except MediaInspectionError as exc:
                raise AppError(
                    "MEDIA_INSPECTION_UNAVAILABLE",
                    "Media inspection is temporarily unavailable",
                    503,
                ) from exc
            duration = metadata.get("duration_seconds")
            if duration is not None and duration > settings.max_media_seconds:
                raise MediaValidationError("media duration exceeds configured limit")
            if source_key != canonical_key:
                try:
                    await store.put_bytes(canonical_key, data, content_type)
                    promoted_key = canonical_key
                except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
                    raise AppError(
                        "ASSET_STORE_UNAVAILABLE",
                        "Object storage is temporarily unavailable",
                        503,
                    ) from exc
                except (AssetStoreError, RuntimeError) as exc:
                    raise _PromotionFailed from exc
            if lost.is_set():
                raise _CompleteClaimLost
    except _CompleteClaimLost:
        if promoted_key:
            await compensate_promoted_object(promoted_key, checksum, len(data))
        raise AppError(
            "ASSET_OPERATION_BUSY",
            "Asset is currently owned by another operation",
            409,
        ) from None
    except AppError:
        await release_complete_claim()
        raise
    except MediaValidationError as exc:
        if await fail_complete_claim() is None:
            raise AppError(
                "ASSET_OPERATION_BUSY",
                "Asset is currently owned by another operation",
                409,
            ) from exc
        raise AppError("ASSET_VALIDATION_FAILED", str(exc), 422) from exc
    except _PromotionFailed as exc:
        if await fail_complete_claim() is None:
            raise AppError(
                "ASSET_OPERATION_BUSY",
                "Asset is currently owned by another operation",
                409,
            ) from exc
        raise AppError(
            "ASSET_PROMOTION_FAILED",
            "Validated upload could not be committed to its immutable object",
            503,
        ) from exc

    lost_ownership = False
    state_conflict = False
    completed: Asset | None = None
    async with session_factory() as phase, phase.begin():
        completed = await phase.get(Asset, asset_id, with_for_update=True)
        if not completed or not owns_claim(completed, claim_id, OUTPUT_WRITE_CLAIM):
            lost_ownership = True
        elif completed.status != "VALIDATING":
            clear_claim(completed)
            state_conflict = True
        else:
            completed.size_bytes = len(data)
            completed.checksum = checksum
            completed.width = metadata.get("width")
            completed.height = metadata.get("height")
            completed.duration_seconds = duration
            completed.status = "READY"
            completed.failed_at = None
            clear_claim(completed)

    if lost_ownership:
        if promoted_key:
            await compensate_promoted_object(promoted_key, checksum, len(data))
        raise AppError(
            "ASSET_OPERATION_BUSY",
            "Asset is currently owned by another operation",
            409,
        )
    if state_conflict:
        if promoted_key:
            await compensate_promoted_object(promoted_key, checksum, len(data))
        raise AppError(
            "ASSET_STATE_CONFLICT", "Asset cannot be completed in its current state", 409
        )
    return completed


def asset_reference_exists(asset_id):
    """Return one SQL predicate covering every durable asset reference."""
    return or_(
        exists(select(1).select_from(GenerationAsset).where(GenerationAsset.asset_id == asset_id)),
        exists(
            select(1)
            .select_from(SceneGeneration)
            .where(SceneGeneration.output_asset_id == asset_id)
        ),
        exists(select(1).select_from(FinalVideo).where(FinalVideo.output_asset_id == asset_id)),
        exists(
            select(1)
            .select_from(FinalVideo)
            .where(FinalVideo.background_audio_asset_id == asset_id)
        ),
        exists(select(1).select_from(FinalVideoScene).where(FinalVideoScene.asset_id == asset_id)),
    )


async def asset_is_referenced(session: AsyncSession, asset_id: str) -> bool:
    return bool(await session.scalar(select(asset_reference_exists(asset_id))))
