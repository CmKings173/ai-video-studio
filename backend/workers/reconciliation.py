"""Repair recoverable MinIO/asset dual-write states and report unsafe drift."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import timedelta

from botocore.exceptions import ClientError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from apps.api.app.db.models import Asset, utcnow
from apps.api.app.integrations.media import (
    MediaInspectionError,
    MediaValidationError,
    inspect_media,
)
from apps.api.app.integrations.minio import (
    AssetObjectMissingError,
    AssetStoreError,
    AssetStoreUnavailableError,
)
from apps.api.app.services.asset_claims import (
    REPAIR_CLAIM,
    acquire_claim,
    claim_heartbeat,
    clear_claim,
    deletion_lifecycle_owns_object,
    owns_claim,
    release_claim,
    requeue_deletion_retry,
)
from apps.api.app.services.asset_retention import AssetRetentionService
from apps.api.app.services.asset_service import upload_staging_key

logger = logging.getLogger(__name__)


_MISSING_CODES = {"404", "NoSuchKey", "NoSuchObject", "NotFound"}
_STORAGE_ERRORS = (
    AssetObjectMissingError,
    AssetStoreUnavailableError,
    AssetStoreError,
    TimeoutError,
    ConnectionError,
    KeyError,
    FileNotFoundError,
    ClientError,
)


def _is_confirmed_missing(exc: BaseException) -> bool:
    if isinstance(exc, (AssetObjectMissingError, KeyError, FileNotFoundError)):
        return True
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", ""))
        if code == "NoSuchBucket":
            return False
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
    compensation_failures: list[str] = field(default_factory=list)
    skipped_claimed_assets: list[str] = field(default_factory=list)


class AssetReconciler:
    def __init__(self, factory, store, settings):
        self.factory = factory
        self.store = store
        self.settings = settings
        self.retention = AssetRetentionService(factory, store, settings)
        self.claim_timeout_seconds = int(
            getattr(settings, "asset_operation_claim_timeout_seconds", 900)
        )

    async def _mark_ready_failed(self, asset_id: str) -> bool:
        async with self.factory() as session, session.begin():
            claim_id = await acquire_claim(
                session,
                asset_id,
                REPAIR_CLAIM,
                timeout_seconds=self.claim_timeout_seconds,
                allowed_statuses={"READY"},
            )
            if claim_id is None:
                return False
            asset = await session.get(Asset, asset_id, with_for_update=True)
            if not asset or not owns_claim(asset, claim_id, REPAIR_CLAIM):
                return False
            asset.status = "FAILED"
            asset.failed_at = utcnow()
            clear_claim(asset)
            return True

    async def _mark_pending_failed(self, asset_id: str) -> bool:
        async with self.factory() as session, session.begin():
            claim_id = await acquire_claim(
                session,
                asset_id,
                REPAIR_CLAIM,
                timeout_seconds=self.claim_timeout_seconds,
                allowed_statuses={"PENDING", "PENDING_UPLOAD", "VALIDATING"},
            )
            if claim_id is None:
                return False
            asset = await session.get(Asset, asset_id, with_for_update=True)
            if not asset or not owns_claim(asset, claim_id, REPAIR_CLAIM):
                return False
            asset.status = "FAILED"
            asset.failed_at = utcnow()
            clear_claim(asset)
            return True

    def _unavailable(
        self, report: ReconciliationReport, snapshot: dict, operation: str, exc: BaseException
    ) -> None:
        report.unavailable_objects.append(snapshot["object_key"])
        logger.warning(
            "asset_reconciliation_storage_unavailable",
            extra={
                "asset_id": snapshot["id"],
                "object_key": snapshot["object_key"],
                "error_type": type(exc).__name__,
                "operation": operation,
            },
        )

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

    async def _release_repair_claim(self, asset_id: str, claim_id: str) -> None:
        async with self.factory() as session, session.begin():
            await release_claim(session, asset_id, claim_id, REPAIR_CLAIM)

    async def _compensate_promoted(
        self,
        asset_id: str,
        claim_id: str,
        object_key: str,
        checksum: str,
        size: int,
        report: ReconciliationReport,
    ) -> None:
        """Delete promoted bytes once retention owns the canonical lifecycle."""
        current_status = "MISSING"
        safe_delete = False
        retry_queued = False
        needs_retry_before_delete = False
        async with self.factory() as session:
            asset = await session.get(Asset, asset_id)
            if asset:
                current_status = asset.status
                safe_delete = deletion_lifecycle_owns_object(asset, object_key)
                needs_retry_before_delete = (
                    asset.status == "DELETED" and asset.purged_at is not None
                )
        if safe_delete and needs_retry_before_delete:
            try:
                retry_queued = await requeue_deletion_retry(
                    self.factory, asset_id, object_key
                )
            except SQLAlchemyError as exc:
                safe_delete = False
                error_type = type(exc).__name__
            else:
                if not retry_queued:
                    safe_delete = False
                    error_type = "RETRY_REQUEUE_FAILED"
        if not safe_delete:
            report.compensation_failures.append(object_key)
            logger.warning(
                "asset_reconciliation_compensation_unresolved",
                extra={
                    "asset_id": asset_id,
                    "object_key": object_key,
                    "claim_id": claim_id,
                    "claim_type": REPAIR_CLAIM,
                    "current_status": current_status,
                    "error_type": locals().get("error_type", "UNSAFE_TO_DELETE"),
                    "operation": "compensate_delete",
                    "retry_queued": retry_queued,
                    "checksum": checksum,
                    "size_bytes": size,
                },
            )
            return
        try:
            await self.store.delete(object_key)
        except (AssetStoreError, OSError, TimeoutError, ConnectionError) as exc:
            try:
                retry_queued = retry_queued or await requeue_deletion_retry(
                    self.factory, asset_id, object_key
                )
            except SQLAlchemyError as retry_exc:
                retry_error = type(retry_exc).__name__
            else:
                retry_error = None
            report.compensation_failures.append(object_key)
            logger.warning(
                "asset_reconciliation_compensation_failed",
                extra={
                    "asset_id": asset_id,
                    "object_key": object_key,
                    "claim_id": claim_id,
                    "claim_type": REPAIR_CLAIM,
                    "error_type": type(exc).__name__,
                    "retry_error_type": retry_error,
                    "operation": "compensate_delete",
                    "retry_queued": retry_queued,
                },
            )
            return

    async def _repair_pending(self, snapshot: dict, report: ReconciliationReport) -> None:
        source_key = snapshot["object_key"]
        try:
            data = await self.store.get_bytes(source_key)
        except (AssetObjectMissingError, KeyError, FileNotFoundError):
            source_key = upload_staging_key(asset_id=snapshot["id"])
            try:
                data = await self.store.get_bytes(source_key)
            except (AssetObjectMissingError, KeyError, FileNotFoundError) as exc:
                now = utcnow()
                created_at = snapshot.get("created_at")
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=now.tzinfo)
                grace_period = timedelta(
                    seconds=getattr(self.settings, "upload_intent_grace_seconds", 900)
                )
                if (
                    snapshot["status"] in {"PENDING", "PENDING_UPLOAD"}
                    and created_at
                    and (created_at + grace_period) > now
                ):
                    return
                if await self._mark_pending_failed(snapshot["id"]):
                    report.missing_objects.append(snapshot["object_key"])
                else:
                    report.skipped_claimed_assets.append(snapshot["id"])
                logger.warning(
                    "asset_reconciliation_object_missing",
                    extra={
                        "asset_id": snapshot["id"],
                        "object_key": source_key,
                        "error_type": type(exc).__name__,
                        "operation": "read",
                    },
                )
                return
            except (
                AssetStoreUnavailableError,
                AssetStoreError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                self._unavailable(report, snapshot, "read_staging", exc)
                return
        except (AssetStoreUnavailableError, AssetStoreError, TimeoutError, ConnectionError) as exc:
            self._unavailable(report, snapshot, "read_primary", exc)
            return

        checksum = hashlib.sha256(data).hexdigest()
        if snapshot["size_bytes"] and len(data) != snapshot["size_bytes"]:
            if await self._mark_pending_failed(snapshot["id"]):
                report.corrupt_objects.append(source_key)
            else:
                report.skipped_claimed_assets.append(snapshot["id"])
            return
        if snapshot["checksum"] and checksum != snapshot["checksum"]:
            if await self._mark_pending_failed(snapshot["id"]):
                report.corrupt_objects.append(source_key)
            else:
                report.skipped_claimed_assets.append(snapshot["id"])
            return
        try:
            metadata = await inspect_media(
                data,
                snapshot["content_type"],
                snapshot["filename"],
                self.settings.ffprobe_binary,
            )
        except MediaValidationError:
            if await self._mark_pending_failed(snapshot["id"]):
                report.corrupt_objects.append(source_key)
            else:
                report.skipped_claimed_assets.append(snapshot["id"])
            return
        except MediaInspectionError as exc:
            self._unavailable(report, snapshot, "inspect_media", exc)
            return

        async with self.factory() as session, session.begin():
            claim_id = await acquire_claim(
                session,
                snapshot["id"],
                REPAIR_CLAIM,
                timeout_seconds=self.claim_timeout_seconds,
                allowed_statuses={"PENDING", "PENDING_UPLOAD", "VALIDATING"},
            )
        if claim_id is None:
            return

        promoted_key: str | None = None
        if source_key != snapshot["object_key"]:
            try:
                async with claim_heartbeat(
                    self.factory,
                    snapshot["id"],
                    claim_id,
                    REPAIR_CLAIM,
                    timeout_seconds=self.claim_timeout_seconds,
                    allowed_statuses={"PENDING", "PENDING_UPLOAD", "VALIDATING"},
                ) as lost:
                    await self.store.put_bytes(
                        snapshot["object_key"], data, snapshot["content_type"]
                    )
                    promoted_key = snapshot["object_key"]
                    if lost.is_set():
                        await self._compensate_promoted(
                            snapshot["id"],
                            claim_id,
                            promoted_key,
                            checksum,
                            len(data),
                            report,
                        )
                        return
            except (
                AssetStoreUnavailableError,
                AssetStoreError,
                TimeoutError,
                ConnectionError,
            ) as exc:
                await self._release_repair_claim(snapshot["id"], claim_id)
                self._unavailable(report, snapshot, "promote", exc)
                return

        should_compensate = False
        async with self.factory() as session, session.begin():
            asset = await session.get(Asset, snapshot["id"], with_for_update=True)
            if not (
                asset
                and owns_claim(asset, claim_id, REPAIR_CLAIM)
                and asset.status in {"PENDING", "PENDING_UPLOAD", "VALIDATING"}
            ):
                should_compensate = promoted_key is not None
                if asset and owns_claim(asset, claim_id, REPAIR_CLAIM):
                    clear_claim(asset)
            else:
                asset.checksum = checksum
                asset.size_bytes = len(data)
                asset.width = metadata.get("width")
                asset.height = metadata.get("height")
                asset.duration_seconds = metadata.get("duration_seconds")
                asset.status = "READY"
                asset.failed_at = None
                clear_claim(asset)
                report.repaired_assets.append(asset.id)

        if should_compensate and promoted_key:
            await self._compensate_promoted(
                snapshot["id"], claim_id, promoted_key, checksum, len(data), report
            )

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
                    except _STORAGE_ERRORS as exc:
                        if _is_confirmed_missing(exc):
                            if await self._mark_ready_failed(snapshot["id"]):
                                report.missing_objects.append(snapshot["object_key"])
                            else:
                                report.skipped_claimed_assets.append(snapshot["id"])
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
                    if await self._mark_ready_failed(snapshot["id"]):
                        report.corrupt_objects.append(snapshot["object_key"])
                    else:
                        report.skipped_claimed_assets.append(snapshot["id"])
            except _STORAGE_ERRORS as exc:
                # A READY row may only be failed on a confirmed not-found result.
                # Unknown/transient failures are reported without mutating business state.
                if _is_confirmed_missing(exc):
                    if await self._mark_ready_failed(snapshot["id"]):
                        report.missing_objects.append(snapshot["object_key"])
                    else:
                        report.skipped_claimed_assets.append(snapshot["id"])
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

    async def cleanup_assets(self, *, dry_run: bool = False) -> list[str]:
        """Use the shared claim protocol for bounded, retryable cleanup."""
        result = await self.retention.cleanup(dry_run=dry_run)
        if dry_run:
            return [item["object_key"] for item in result.candidates]
        return list(result.deleted_keys)

    async def run_once(self) -> bool:
        report = await self.run()
        await self.cleanup_staging()
        await self.cleanup_assets()
        if (
            report.missing_objects
            or report.corrupt_objects
            or report.unavailable_objects
            or report.orphan_objects
            or report.repaired_assets
            or report.compensation_failures
            or report.skipped_claimed_assets
        ):
            logger.warning(
                "asset_reconciliation_drift",
                extra={
                    "missing_objects": report.missing_objects,
                    "corrupt_objects": report.corrupt_objects,
                    "unavailable_objects": report.unavailable_objects,
                    "orphan_objects": report.orphan_objects,
                    "repaired_assets": report.repaired_assets,
                    "compensation_failures": report.compensation_failures,
                    "skipped_claimed_assets": report.skipped_claimed_assets,
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
