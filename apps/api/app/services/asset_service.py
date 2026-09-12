from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
) -> Asset:
    if asset.status == "READY":
        if (
            payload.checksum_sha256
            and asset.checksum
            and payload.checksum_sha256.lower() != asset.checksum.lower()
        ):
            raise AppError(
                "ASSET_CHECKSUM_CONFLICT",
                "Completed asset checksum does not match this request",
                409,
            )
        return asset
    if asset.status not in {"PENDING", "PENDING_UPLOAD", "VALIDATING", "FAILED"}:
        raise AppError(
            "ASSET_STATE_CONFLICT", "Asset cannot be completed in its current state", 409
        )
    asset.status = "VALIDATING"
    asset.failed_at = None
    await session.flush()
    staging_key = upload_staging_key(asset)
    try:
        try:
            head = await store.head(staging_key)
            source_key = staging_key
        except (AssetObjectMissingError, KeyError, FileNotFoundError):
            # A reconciler may have promoted the object before this request
            # committed. The immutable key is a valid recovery source.
            try:
                head = await store.head(asset.object_key)
            except (AssetObjectMissingError, KeyError, FileNotFoundError) as exc:
                raise MediaValidationError("uploaded object is missing") from exc
            except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
                raise AppError(
                    "ASSET_STORE_UNAVAILABLE",
                    "Object storage is temporarily unavailable",
                    503,
                ) from exc
            source_key = asset.object_key
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
        if head["size"] != asset.size_bytes:
            raise MediaValidationError("uploaded object size does not match request")
        if head["content_type"].split(";", 1)[0] != asset.content_type:
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
                data, asset.content_type, asset.filename, settings.ffprobe_binary
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
    except AppError:
        raise
    except MediaValidationError as exc:
        asset.status = "FAILED"
        asset.failed_at = datetime.now(UTC)
        raise AppError("ASSET_VALIDATION_FAILED", str(exc), 422) from exc
    if source_key != asset.object_key:
        try:
            await store.put_bytes(asset.object_key, data, asset.content_type)
        except (AssetStoreUnavailableError, TimeoutError, ConnectionError) as exc:
            raise AppError(
                "ASSET_STORE_UNAVAILABLE",
                "Object storage is temporarily unavailable",
                503,
            ) from exc
        except (AssetStoreError, RuntimeError) as exc:
            asset.status = "FAILED"
            asset.failed_at = datetime.now(UTC)
            raise AppError(
                "ASSET_PROMOTION_FAILED",
                "Validated upload could not be committed to its immutable object",
                503,
            ) from exc
    asset.size_bytes = len(data)
    asset.checksum = checksum
    asset.width = metadata.get("width")
    asset.height = metadata.get("height")
    asset.duration_seconds = duration
    asset.status = "READY"
    asset.failed_at = None
    return asset


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
