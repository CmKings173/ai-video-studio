"""Durable upload validation using existing asset rows and OUTPUT_WRITE leases."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from sqlalchemy import or_, select

from apps.api.app.db.models import Asset, utcnow
from apps.api.app.integrations.media import MediaInspectionError, MediaValidationError
from apps.api.app.integrations.minio import (
    AssetObjectMissingError,
    AssetStoreError,
    AssetStoreUnavailableError,
)
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    claim_available_expression,
    claim_heartbeat,
    claim_is_active,
    clear_claim,
    owns_claim,
    requeue_deletion_retry,
)
from apps.api.app.services.asset_service import upload_staging_key

logger = logging.getLogger(__name__)


class ValidationLeaseLost(Exception):
    pass


@dataclass(frozen=True)
class ValidationClaim:
    asset_id: str
    claim_id: str
    object_key: str
    content_type: str
    size_bytes: int
    expected_checksum: str | None
    verified_checksum: str | None = None


class AssetValidationWorker:
    def __init__(self, factory, store, settings):
        self.factory = factory
        self.store = store
        self.settings = settings
        self.claim_timeout = getattr(settings, "asset_operation_claim_timeout_seconds", 900)
        self.max_attempts = getattr(settings, "asset_validation_max_attempts", 3)
        self.retry_seconds = getattr(settings, "asset_validation_retry_seconds", 10)
        self.timeout = getattr(settings, "asset_validation_timeout_seconds", 300)

    async def claim(self) -> ValidationClaim | None:
        now = utcnow()
        validation = Asset.media_metadata["validation"]
        async with self.factory() as session, session.begin():
            row = await session.scalar(
                select(Asset)
                .where(
                    Asset.status == "VALIDATING",
                    Asset.deleted_at.is_(None),
                    validation["phase"].as_string().in_(["QUEUED", "RUNNING"]),
                    or_(
                        validation["next_attempt_at"].as_string().is_(None),
                        validation["next_attempt_at"].as_string() <= now.isoformat(),
                    ),
                    claim_available_expression(Asset, now=now, timeout_seconds=self.claim_timeout),
                )
                .order_by(Asset.updated_at, Asset.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            envelope = dict(row.media_metadata["validation"])
            attempts = int(envelope.get("attempts", 0))
            if attempts >= self.max_attempts:
                row.status = "FAILED"
                row.failed_at = now
                envelope.update(
                    phase="FAILED",
                    next_attempt_at=None,
                    error={"code": "VALIDATION_ATTEMPTS_EXHAUSTED", "message": "Lease expired"},
                )
                clear_claim(row)
                row.media_metadata = {**row.media_metadata, "validation": envelope}
                return None
            row.operation_claim_id = str(uuid4())
            row.operation_claim_type = OUTPUT_WRITE_CLAIM
            row.operation_claimed_at = now
            envelope.update(phase="RUNNING", attempts=attempts + 1, next_attempt_at=None)
            row.media_metadata = {**row.media_metadata, "validation": envelope}
            return ValidationClaim(
                row.id,
                row.operation_claim_id,
                row.object_key,
                row.content_type,
                row.size_bytes,
                envelope.get("expected_checksum"),
                envelope.get("verified_checksum"),
            )

    def _owned(self, row, job, lost) -> bool:
        return bool(
            not lost.is_set()
            and row
            and row.status == "VALIDATING"
            and row.deleted_at is None
            and owns_claim(row, job.claim_id, OUTPUT_WRITE_CLAIM)
            and claim_is_active(row, now=utcnow(), timeout_seconds=self.claim_timeout)
        )

    async def _check_lease(self, job, lost, checksum):
        async with self.factory() as session, session.begin():
            row = await session.get(Asset, job.asset_id, with_for_update=True)
            if not self._owned(row, job, lost):
                raise ValidationLeaseLost
            # Commit the validated digest before external promotion. It permits
            # verified canonical recovery even when the browser omitted a digest.
            envelope = dict(row.media_metadata["validation"])
            envelope["verified_checksum"] = checksum
            row.media_metadata = {**row.media_metadata, "validation": envelope}

    async def _failure(self, job, code, message, *, retryable):
        async with self.factory() as session, session.begin():
            row = await session.get(Asset, job.asset_id, with_for_update=True)
            if (
                not row
                or row.status != "VALIDATING"
                or not owns_claim(row, job.claim_id, OUTPUT_WRITE_CLAIM)
            ):
                return
            envelope = dict(row.media_metadata["validation"])
            retry = retryable and envelope["attempts"] < self.max_attempts
            envelope.update(
                phase="QUEUED" if retry else "FAILED",
                next_attempt_at=(
                    utcnow()
                    + timedelta(seconds=self.retry_seconds * 2 ** (envelope["attempts"] - 1))
                ).isoformat()
                if retry
                else None,
                error={"code": code, "message": message[:500]},
            )
            row.media_metadata = {**row.media_metadata, "validation": envelope}
            if not retry:
                row.status = "FAILED"
                row.failed_at = utcnow()
            clear_claim(row)

    async def _compensate(self, job):
        # Never delete bytes which a replacement worker could have adopted. When
        # deletion owns the row, durably reopen retention cleanup, including the
        # race where retention already purged before a late upload completed.
        queued = await requeue_deletion_retry(self.factory, job.asset_id, job.object_key)
        logger.warning(
            "asset_validation_promotion_uncommitted",
            extra={"asset_id": job.asset_id, "deletion_retry_queued": queued},
        )

    async def _validate(self, job, lost):
        # Import at execution time so enqueue never imports or invokes media tooling.
        from apps.api.app.integrations.media import inspect_media_path

        root = Path(self.settings.workspace_root) / "asset-validation"
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        required = self.settings.max_upload_bytes + getattr(self.settings, "min_free_disk_bytes", 0)
        if (await asyncio.to_thread(shutil.disk_usage, root)).free < required:
            raise OSError("Insufficient temporary disk space for asset validation")
        with TemporaryDirectory(prefix=f"{job.asset_id}-", dir=root) as directory:
            path = Path(directory) / "source"
            source_key = (
                job.object_key
                if job.verified_checksum
                else upload_staging_key(asset_id=job.asset_id)
            )
            try:
                info = await self.store.download_to_path(
                    source_key,
                    path,
                    max_bytes=self.settings.max_upload_bytes,
                )
            except (AssetObjectMissingError, KeyError, FileNotFoundError):
                if source_key == job.object_key:
                    recovery_key = upload_staging_key(asset_id=job.asset_id)
                elif job.expected_checksum:
                    recovery_key = job.object_key
                else:
                    raise
                info = await self.store.download_to_path(
                    recovery_key,
                    path,
                    max_bytes=self.settings.max_upload_bytes,
                )
            size = info["size"]
            if size <= 0 or size > self.settings.max_upload_bytes or size != job.size_bytes:
                raise MediaValidationError("uploaded object size does not match request or limit")
            if info["content_type"].split(";", 1)[0] != job.content_type:
                raise MediaValidationError("uploaded content type does not match request")
            checksum = info["checksum"].lower()
            if job.expected_checksum and checksum != job.expected_checksum:
                raise MediaValidationError("uploaded checksum does not match")
            if job.verified_checksum and checksum != job.verified_checksum:
                raise MediaValidationError("uploaded checksum differs from validated promotion")
            metadata = await inspect_media_path(
                path, job.content_type, self.settings.ffprobe_binary
            )
            duration = metadata.get("duration_seconds")
            if duration is not None and duration > self.settings.max_media_seconds:
                raise MediaValidationError("media duration exceeds configured limit")
            await self._check_lease(job, lost, checksum)
            # Compensate even uncertain failures: storage may have accepted the
            # immutable write before its transport response was interrupted.
            try:
                await self.store.put_file_immutable(
                    job.object_key,
                    path,
                    job.content_type,
                    checksum,
                    max_bytes=self.settings.max_upload_bytes,
                )
                async with self.factory() as session, session.begin():
                    row = await session.get(Asset, job.asset_id, with_for_update=True)
                    if not self._owned(row, job, lost):
                        raise ValidationLeaseLost
                    envelope = dict(row.media_metadata["validation"])
                    envelope.update(phase="READY", error=None, next_attempt_at=None)
                    row.media_metadata = {
                        **metadata,
                        "checksum": checksum,
                        "size_bytes": size,
                        "validation": envelope,
                    }
                    row.checksum = checksum
                    row.size_bytes = size
                    row.width = metadata.get("width")
                    row.height = metadata.get("height")
                    row.duration_seconds = duration
                    row.status = "READY"
                    row.failed_at = None
                    clear_claim(row)
            except BaseException:
                await self._compensate(job)
                raise

    async def process(self, job):
        async with claim_heartbeat(
            self.factory,
            job.asset_id,
            job.claim_id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=self.claim_timeout,
            allowed_statuses={"VALIDATING"},
        ) as lost:
            try:
                async with asyncio.timeout(self.timeout):
                    await self._validate(job, lost)
            except ValidationLeaseLost:
                logger.warning("asset_validation_lease_lost", extra={"asset_id": job.asset_id})
            except (AssetObjectMissingError, KeyError, FileNotFoundError) as exc:
                await self._failure(job, "ASSET_OBJECT_MISSING", str(exc), retryable=False)
            except MediaValidationError as exc:
                await self._failure(job, "ASSET_VALIDATION_FAILED", str(exc), retryable=False)
            except MediaInspectionError as exc:
                await self._failure(job, "MEDIA_INSPECTION_UNAVAILABLE", str(exc), retryable=True)
            except (AssetStoreUnavailableError, TimeoutError, ConnectionError, OSError) as exc:
                await self._failure(job, "ASSET_VALIDATION_UNAVAILABLE", str(exc), retryable=True)
            except AssetStoreError as exc:
                await self._failure(job, "ASSET_STORAGE_INVALID", str(exc), retryable=False)
            except asyncio.CancelledError:
                # Crash/shutdown keeps RUNNING and the lease; takeover consumes
                # another bounded attempt once heartbeat expires.
                raise
            except Exception as exc:
                logger.exception("asset_validation_worker_error", extra={"asset_id": job.asset_id})
                await self._failure(job, "ASSET_VALIDATION_WORKER_ERROR", str(exc), retryable=True)

    async def run_once(self):
        job = await self.claim()
        if job is None:
            return False
        await self.process(job)
        return True


async def main():
    from apps.api.app.core.config import get_settings
    from apps.api.app.db.session import SessionFactory
    from apps.api.app.integrations.minio import AssetStore
    from workers.common import service_loop

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    logger.info("asset_validation_worker_started")
    await service_loop(
        AssetValidationWorker(SessionFactory, AssetStore(settings), settings),
        settings.worker_poll_seconds,
    )


if __name__ == "__main__":
    asyncio.run(main())
