"""Repair recoverable MinIO/asset dual-write states and report unsafe drift."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from botocore.exceptions import ClientError
from sqlalchemy import select

from apps.api.app.db.models import Asset, utcnow
from apps.api.app.integrations.media import inspect_media
from apps.api.app.integrations.minio import AssetObjectMissingError
from apps.api.app.services.asset_service import upload_staging_key

logger = logging.getLogger(__name__)


_MISSING_CODES = {"404", "NoSuchKey", "NoSuchObject", "NoSuchBucket", "NotFound"}


def _is_confirmed_missing(exc: BaseException) -> bool:
    if isinstance(exc, (AssetObjectMissingError, KeyError, FileNotFoundError)):
        return True
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return code in _MISSING_CODES or status == 404
    return False


@dataclass
class ReconciliationReport:
    missing_objects: list[str] = field(default_factory=list)
    corrupt_objects: list[str] = field(default_factory=list)
    unavailable_objects: list[str] = field(default_factory=list)
    orphan_objects: list[str] = field(default_factory=list)
    repaired_assets: list[str] = field(default_factory=list)


class AssetReconciler:
    def __init__(self, factory, store, settings):
        self.factory = factory
        self.store = store
        self.settings = settings

    async def _mark_ready_failed(self, asset_id: str) -> None:
        async with self.factory() as session, session.begin():
            asset = await session.get(Asset, asset_id, with_for_update=True)
            if asset and asset.status == "READY":
                asset.status = "FAILED"

    async def _snapshots(self) -> list[dict]:
        async with self.factory() as session:
            rows = list(
                (await session.scalars(select(Asset).where(Asset.deleted_at.is_(None)))).all()
            )
            return [
                {
                    "id": row.id,
                    "status": row.status,
                    "object_key": row.object_key,
                    "checksum": row.checksum,
                    "size_bytes": row.size_bytes,
                    "content_type": row.content_type,
                    "filename": row.filename,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    async def _repair_pending(self, snapshot: dict, report: ReconciliationReport) -> None:
        source_key = snapshot["object_key"]
        try:
            data = await self.store.get_bytes(source_key)
        except Exception:
            source_key = upload_staging_key(asset_id=snapshot["id"])
            try:
                data = await self.store.get_bytes(source_key)
            except Exception:
                return
        checksum = hashlib.sha256(data).hexdigest()
        if snapshot["size_bytes"] and len(data) != snapshot["size_bytes"]:
            report.corrupt_objects.append(source_key)
            async with self.factory() as session, session.begin():
                asset = await session.get(Asset, snapshot["id"], with_for_update=True)
                if asset and asset.status in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
                    asset.status = "FAILED"
            return
        if snapshot["checksum"] and checksum != snapshot["checksum"]:
            report.corrupt_objects.append(source_key)
            async with self.factory() as session, session.begin():
                asset = await session.get(Asset, snapshot["id"], with_for_update=True)
                if asset and asset.status in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
                    asset.status = "FAILED"
            return
        try:
            metadata = await inspect_media(
                data,
                snapshot["content_type"],
                snapshot["filename"],
                self.settings.ffprobe_binary,
            )
        except Exception:
            report.corrupt_objects.append(source_key)
            async with self.factory() as session, session.begin():
                asset = await session.get(Asset, snapshot["id"], with_for_update=True)
                if asset and asset.status in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
                    asset.status = "FAILED"
            return
        if source_key != snapshot["object_key"]:
            try:
                await self.store.put_bytes(snapshot["object_key"], data, snapshot["content_type"])
            except Exception:
                report.corrupt_objects.append(snapshot["object_key"])
                return
        async with self.factory() as session, session.begin():
            asset = await session.get(Asset, snapshot["id"], with_for_update=True)
            if asset is None or asset.status not in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
                return
            asset.checksum = checksum
            asset.size_bytes = len(data)
            asset.width = metadata.get("width")
            asset.height = metadata.get("height")
            asset.duration_seconds = metadata.get("duration_seconds")
            asset.status = "READY"
            report.repaired_assets.append(asset.id)

    async def run(self, *, inspect_orphans: bool = True) -> ReconciliationReport:
        report = ReconciliationReport()
        snapshots = await self._snapshots()
        known_keys = {item["object_key"] for item in snapshots}
        known_keys.update(
            upload_staging_key(asset_id=item["id"])
            for item in snapshots
            if item["status"] in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}
        )
        for snapshot in snapshots:
            if snapshot["status"] in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}:
                await self._repair_pending(snapshot, report)
                continue
            if snapshot["status"] != "READY":
                continue
            try:
                head = await self.store.head(snapshot["object_key"])
                corrupt = head["size"] != snapshot["size_bytes"]
                if snapshot["checksum"] and head.get("checksum") != snapshot["checksum"]:
                    try:
                        data = await self.store.get_bytes(
                            snapshot["object_key"], self.settings.max_upload_bytes
                        )
                        corrupt = hashlib.sha256(data).hexdigest() != snapshot["checksum"]
                    except Exception as exc:
                        if _is_confirmed_missing(exc):
                            report.missing_objects.append(snapshot["object_key"])
                            await self._mark_ready_failed(snapshot["id"])
                        else:
                            report.unavailable_objects.append(snapshot["object_key"])
                            logger.warning(
                                "asset_reconciliation_storage_unavailable",
                                extra={
                                    "object_key": snapshot["object_key"],
                                    "error_type": type(exc).__name__,
                                },
                            )
                        continue
                if corrupt:
                    report.corrupt_objects.append(snapshot["object_key"])
                    await self._mark_ready_failed(snapshot["id"])
            except Exception as exc:
                # A READY row may only be failed on a confirmed not-found result.
                # Unknown/transient failures are reported without mutating business state.
                if _is_confirmed_missing(exc):
                    report.missing_objects.append(snapshot["object_key"])
                    await self._mark_ready_failed(snapshot["id"])
                else:
                    report.unavailable_objects.append(snapshot["object_key"])
                    logger.warning(
                        "asset_reconciliation_storage_unavailable",
                        extra={
                            "object_key": snapshot["object_key"],
                            "error_type": type(exc).__name__,
                        },
                    )
        if inspect_orphans:
            for item in await self.store.list_objects():
                if item["key"] not in known_keys:
                    report.orphan_objects.append(item["key"])
        return report

    async def cleanup_staging(self) -> list[str]:
        cutoff = utcnow() - timedelta(hours=self.settings.orphan_object_retention_hours)
        deleted: list[str] = []
        active_staging = {
            upload_staging_key(asset_id=item["id"])
            for item in await self._snapshots()
            if item["status"] in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}
        }
        for item in await self.store.list_objects("staging/"):
            if item["key"] in active_staging:
                continue
            modified = item.get("last_modified")
            if modified and modified.tzinfo is None:
                modified = modified.replace(tzinfo=cutoff.tzinfo)
            if modified and modified < cutoff:
                await self.store.delete(item["key"])
                deleted.append(item["key"])
        return deleted

    async def run_once(self) -> bool:
        report = await self.run()
        await self.cleanup_staging()
        if (
            report.missing_objects
            or report.corrupt_objects
            or report.unavailable_objects
            or report.orphan_objects
            or report.repaired_assets
        ):
            logger.warning(
                "asset_reconciliation_drift",
                extra={
                    "missing_objects": report.missing_objects,
                    "corrupt_objects": report.corrupt_objects,
                    "unavailable_objects": report.unavailable_objects,
                    "orphan_objects": report.orphan_objects,
                    "repaired_assets": report.repaired_assets,
                },
            )
        return False


async def main() -> None:
    from apps.api.app.core.config import get_settings
    from apps.api.app.db.session import SessionFactory
    from apps.api.app.integrations.minio import AssetStore

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    reconciler = AssetReconciler(SessionFactory, AssetStore(settings), settings)
    while True:
        try:
            await reconciler.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("asset_reconciliation_failed")
        await asyncio.sleep(settings.reconciliation_interval_seconds)


if __name__ == "__main__":
    asyncio.run(main())
