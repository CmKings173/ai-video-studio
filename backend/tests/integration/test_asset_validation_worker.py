from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Project, User, utcnow
from apps.api.app.integrations.media import MediaInspectionError, MediaValidationError
from apps.api.app.integrations.minio import AssetStoreError, AssetStoreUnavailableError
from apps.api.app.services.asset_service import (
    complete_asset,
    enqueue_asset_validation,
    upload_staging_key,
)
from workers.asset_validation import AssetValidationWorker
from workers.reconciliation import AssetReconciler, ReconciliationReport


def settings(tmp_path):
    return SimpleNamespace(
        workspace_root=tmp_path,
        max_upload_bytes=4096,
        max_media_seconds=600,
        min_free_disk_bytes=0,
        ffprobe_binary="ffprobe",
        asset_operation_claim_timeout_seconds=60,
        asset_validation_max_attempts=3,
        asset_validation_retry_seconds=30,
        asset_validation_timeout_seconds=30,
        pending_asset_retention_hours=24,
        retention_failed_hours=24,
        deleted_asset_retention_hours=168,
        retention_deleting_retry_hours=1,
        retention_batch_size=10,
    )


def png():
    output = BytesIO()
    Image.new("RGB", (8, 6), "red").save(output, "PNG")
    return output.getvalue()


async def seed(factory, data=None):
    data = png() if data is None else data
    async with factory() as session, session.begin():
        user = User(email=f"validation-{utcnow().timestamp()}@test", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Validation", created_by=user.id)
        session.add(project)
        await session.flush()
        row = Asset(
            project_id=project.id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="test.png",
            content_type="image/png",
            object_key=f"assets/{project.id}/source.png",
            status="PENDING_UPLOAD",
            size_bytes=len(data),
            created_by=user.id,
        )
        session.add(row)
        await session.flush()
        return row.id, user.id, row.object_key


async def enqueue(factory, config, asset_id, user_id, checksum=None, retry=False):
    async with factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        return await enqueue_asset_validation(
            session,
            config,
            row,
            SimpleNamespace(checksum_sha256=checksum, retry_validation=retry),
            user_id=user_id,
        )


async def read(factory, asset_id):
    async with factory() as session:
        return await session.get(Asset, asset_id)


class DiskStore:
    def __init__(self, asset_id, data=None):
        self.objects = {
            upload_staging_key(asset_id=asset_id): (png() if data is None else data, "image/png")
        }
        self.download_error = None
        self.promotion_error = None
        self.before_download = None
        self.before_promotion = None
        self.after_promotion = None
        self.downloads = 0
        self.promotions = 0
        self.paths = []

    async def download_to_path(self, key, path, max_bytes):
        self.downloads += 1
        self.paths.append(path)
        if self.before_download:
            await self.before_download()
        if self.download_error:
            raise self.download_error
        data, content_type = self.objects[key]
        if len(data) > max_bytes:
            raise AssetStoreError("Object exceeds permitted size")
        await asyncio.to_thread(Path(path).write_bytes, data)
        return {
            "size": len(data),
            "checksum": hashlib.sha256(data).hexdigest(),
            "content_type": content_type,
        }

    async def put_file_immutable(self, key, path, content_type, checksum, max_bytes):
        self.promotions += 1
        if self.before_promotion:
            await self.before_promotion()
        if self.promotion_error:
            raise self.promotion_error
        data = await asyncio.to_thread(Path(path).read_bytes)
        assert len(data) <= max_bytes and hashlib.sha256(data).hexdigest() == checksum
        if key in self.objects and self.objects[key] != (data, content_type):
            raise AssetStoreError("Immutable object key already contains different data")
        self.objects[key] = (data, content_type)
        if self.after_promotion:
            await self.after_promotion()


@pytest.mark.asyncio
async def test_success_duplicate_ready_replay_and_checksum_binding(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, canonical = await seed(session_factory)
    digest = hashlib.sha256(png()).hexdigest()
    await enqueue(session_factory, config, asset_id, user_id, digest.upper())
    await enqueue(session_factory, config, asset_id, user_id, digest)
    with pytest.raises(AppError, match="Queued checksum differs"):
        await enqueue(session_factory, config, asset_id, user_id, "a" * 64)
    store = DiskStore(asset_id)
    worker = AssetValidationWorker(session_factory, store, config)
    assert await worker.run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "READY" and row.checksum == digest and row.width == 8
    assert row.media_metadata["validation"]["attempts"] == 1
    assert row.operation_claim_id is None
    assert canonical in store.objects
    assert all(not path.exists() for path in store.paths)
    assert (await enqueue(session_factory, config, asset_id, user_id, digest)).status == "READY"
    assert not await worker.run_once()
    assert store.promotions == 1
    with pytest.raises(AppError, match="Completed checksum differs"):
        await enqueue(session_factory, config, asset_id, user_id, "a" * 64)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "checksum",
        "content_type",
        "oversized",
        "size",
        "corrupt",
        "collision",
    ],
)
async def test_permanent_failures(session_factory, tmp_path, failure):
    config = settings(tmp_path)
    asset_id, user_id, canonical = await seed(session_factory)
    store = DiskStore(asset_id)
    digest = "a" * 64 if failure == "checksum" else None
    if failure == "missing":
        store.objects.clear()
    elif failure == "content_type":
        store.objects[upload_staging_key(asset_id=asset_id)] = (png(), "image/jpeg")
    elif failure == "oversized":
        config.max_upload_bytes = 10
    elif failure == "size":
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id)
            row.size_bytes += 1
    elif failure == "corrupt":
        store.objects[upload_staging_key(asset_id=asset_id)] = (b"x" * len(png()), "image/png")
    elif failure == "collision":
        store.objects[canonical] = (b"preexisting immutable", "image/png")
    await enqueue(session_factory, config, asset_id, user_id, digest)
    worker = AssetValidationWorker(session_factory, store, config)
    assert await worker.run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "FAILED" and row.failed_at is not None
    assert row.operation_claim_id is None
    assert row.media_metadata["validation"]["error"]
    assert row.media_metadata["validation"]["expected_checksum"] == digest
    assert all(not path.exists() for path in store.paths)
    assert not await worker.run_once()
    if failure == "collision":
        assert store.objects[canonical][0] == b"preexisting immutable"
        assert row.media_metadata["validation"]["error"]["code"] == "ASSET_STORAGE_INVALID"
    else:
        assert store.promotions == 0
    if failure == "oversized":
        assert row.media_metadata["validation"]["error"]["code"] == "ASSET_STORAGE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["storage", "ffprobe", "promotion", "timeout", "disk"])
async def test_transient_failure_backoff_bounded_retries(
    session_factory, tmp_path, monkeypatch, failure
):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    store = DiskStore(asset_id)
    if failure == "storage":
        store.download_error = AssetStoreUnavailableError("temporary outage")
    elif failure == "promotion":
        store.promotion_error = TimeoutError("promotion unavailable")
    elif failure == "ffprobe":

        async def unavailable(*args):
            raise MediaInspectionError("ffprobe unavailable")

        monkeypatch.setattr("apps.api.app.integrations.media.inspect_media_path", unavailable)
    elif failure == "timeout":

        async def slow():
            await asyncio.sleep(1)

        store.before_download = slow
        config.asset_validation_timeout_seconds = 0.01
    elif failure == "disk":
        monkeypatch.setattr(
            "workers.asset_validation.shutil.disk_usage", lambda path: SimpleNamespace(free=0)
        )
    worker = AssetValidationWorker(session_factory, store, config)
    for attempt in range(1, 4):
        assert await worker.run_once()
        row = await read(session_factory, asset_id)
        assert row.media_metadata["validation"]["attempts"] == attempt
        assert row.operation_claim_id is None
        if attempt < 3:
            assert row.status == "VALIDATING"
            assert row.media_metadata["validation"]["phase"] == "QUEUED"
            assert row.media_metadata["validation"]["next_attempt_at"] > utcnow().isoformat()
            assert not await worker.run_once()
            async with session_factory() as session, session.begin():
                row = await session.get(Asset, asset_id)
                envelope = dict(row.media_metadata["validation"])
                envelope["next_attempt_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
                row.media_metadata = {"validation": envelope}
        else:
            assert row.status == "FAILED"
    assert all(not path.exists() for path in store.paths)


@pytest.mark.asyncio
async def test_failed_retry_is_explicit_and_preserves_checksum(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    digest = hashlib.sha256(png()).hexdigest()
    await enqueue(session_factory, config, asset_id, user_id, digest)
    store = DiskStore(asset_id)
    store.download_error = MediaValidationError("corrupt")
    worker = AssetValidationWorker(session_factory, store, config)
    await worker.run_once()
    with pytest.raises(AppError, match="retry_validation"):
        await enqueue(session_factory, config, asset_id, user_id, digest)
    with pytest.raises(AppError, match="Queued checksum differs"):
        await enqueue(session_factory, config, asset_id, user_id, "a" * 64, retry=True)
    await enqueue(session_factory, config, asset_id, user_id, digest, retry=True)
    store.download_error = None
    await worker.run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "READY" and row.media_metadata["validation"]["attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before_promotion", "after_promotion"])
async def test_lost_lease_cannot_promote_or_commit(session_factory, tmp_path, stage):
    config = settings(tmp_path)
    asset_id, user_id, canonical = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    store = DiskStore(asset_id)

    async def steal():
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id)
            row.operation_claim_id = "replacement"

    if stage == "before_promotion":
        store.before_download = steal
    else:
        store.after_promotion = steal
    await AssetValidationWorker(session_factory, store, config).run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "VALIDATING" and row.operation_claim_id == "replacement"
    assert row.checksum is None
    assert store.promotions == (0 if stage == "before_promotion" else 1)
    if stage == "after_promotion":
        assert canonical in store.objects  # replacement can adopt immutable bytes
    assert all(not path.exists() for path in store.paths)


@pytest.mark.asyncio
async def test_heartbeat_db_failure_fences_promotion(session_factory, tmp_path, monkeypatch):
    config = settings(tmp_path)
    config.asset_operation_claim_timeout_seconds = 0.03
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    failure = asyncio.Event()

    async def failed_renew(*args, **kwargs):
        failure.set()
        raise OperationalError("renew claim", {}, RuntimeError("connection reset"))

    monkeypatch.setattr("apps.api.app.services.asset_claims.renew_claim", failed_renew)
    store = DiskStore(asset_id)

    async def wait_failure():
        await asyncio.wait_for(failure.wait(), timeout=1)

    store.before_download = wait_failure
    await AssetValidationWorker(session_factory, store, config).run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "VALIDATING" and row.operation_claim_id
    assert store.promotions == 0


@pytest.mark.asyncio
async def test_crash_reclaim_and_exhausted_stale_claim(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    worker = AssetValidationWorker(session_factory, DiskStore(asset_id), config)
    first = await worker.claim()
    assert first
    assert await worker.claim() is None
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.operation_claimed_at = utcnow() - timedelta(seconds=61)
    second = await worker.claim()
    assert second and second.claim_id != first.claim_id
    await worker.process(first)
    row = await read(session_factory, asset_id)
    assert row.status == "VALIDATING" and row.operation_claim_id == second.claim_id
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.operation_claimed_at = utcnow() - timedelta(seconds=61)
    third = await worker.claim()
    assert third
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.operation_claimed_at = utcnow() - timedelta(seconds=61)
    assert await worker.claim() is None
    row = await read(session_factory, asset_id)
    assert row.status == "FAILED" and row.operation_claim_id is None
    assert row.media_metadata["validation"]["attempts"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("with_checksum", [True, False])
@pytest.mark.parametrize("staging_state", ["missing", "changed"])
async def test_crash_after_promotion_replay_adopts_identical_object(
    session_factory, tmp_path, with_checksum, staging_state
):
    config = settings(tmp_path)
    asset_id, user_id, canonical = await seed(session_factory)
    digest = hashlib.sha256(png()).hexdigest()
    await enqueue(session_factory, config, asset_id, user_id, digest if with_checksum else None)
    store = DiskStore(asset_id)
    worker = AssetValidationWorker(session_factory, store, config)
    first = await worker.claim()

    async def crash():
        raise asyncio.CancelledError

    store.after_promotion = crash
    with pytest.raises(asyncio.CancelledError):
        await worker.process(first)
    assert canonical in store.objects
    assert all(not path.exists() for path in store.paths)
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.operation_claimed_at = utcnow() - timedelta(seconds=61)
    store.after_promotion = None
    if staging_state == "missing":
        del store.objects[upload_staging_key(asset_id=asset_id)]
    else:
        store.objects[upload_staging_key(asset_id=asset_id)] = (b"changed bytes", "image/png")
    assert await worker.run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "READY" and row.media_metadata["validation"]["attempts"] == 2


@pytest.mark.asyncio
async def test_deletion_after_promotion_reopens_purged_retention(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    store = DiskStore(asset_id)

    async def delete():
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id)
            row.status = "DELETED"
            row.deleted_at = row.purged_at = utcnow()
            row.operation_claim_id = row.operation_claim_type = row.operation_claimed_at = None

    store.after_promotion = delete
    await AssetValidationWorker(session_factory, store, config).run_once()
    row = await read(session_factory, asset_id)
    assert row.status == "DELETING" and row.purged_at is None
    assert row.delete_claimed_at is not None


@pytest.mark.asyncio
async def test_reconciler_rechecks_envelope_under_lock(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, canonical = await seed(session_factory)
    data = png()

    class RacingStore:
        async def get_bytes(self, key):
            # Enqueue after reconciler's initial check and stale snapshot read.
            await enqueue(session_factory, config, asset_id, user_id, "a" * 64)
            return data

        async def put_bytes(self, *args):
            pytest.fail("Reconciler bypassed validation checksum")

    reconciler = AssetReconciler(session_factory, RacingStore(), config)
    snapshot = (await reconciler._snapshots())[0]
    await reconciler._repair_pending(snapshot, ReconciliationReport())
    assert not await reconciler._mark_pending_failed(asset_id)
    row = await read(session_factory, asset_id)
    assert row.status == "VALIDATING" and row.operation_claim_id is None
    assert row.object_key == canonical
    assert row.media_metadata["validation"]["expected_checksum"] == "a" * 64
    await reconciler._repair_pending(snapshot, ReconciliationReport())


@pytest.mark.asyncio
async def test_enqueue_rollback_publishes_no_work(session_factory, tmp_path):
    asset_id, user_id, _ = await seed(session_factory)
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        await enqueue_asset_validation(
            session,
            settings(tmp_path),
            row,
            SimpleNamespace(checksum_sha256=None),
            user_id=user_id,
        )
        await session.rollback()
    row = await read(session_factory, asset_id)
    assert row.status == "PENDING_UPLOAD" and "validation" not in row.media_metadata


@pytest.mark.asyncio
async def test_legacy_complete_cannot_bypass_durable_checksum(session_factory, tmp_path):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id, "a" * 64)
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        with pytest.raises(AppError, match="Durable validation"):
            await complete_asset(
                session,
                object(),
                config,
                row,
                SimpleNamespace(checksum_sha256=None),
                factory=session_factory,
            )
    row = await read(session_factory, asset_id)
    assert row.status == "VALIDATING" and row.operation_claim_id is None


@pytest.mark.asyncio
async def test_claim_compiles_postgresql_skip_locked(session_factory, tmp_path):
    # SQLite exercises lifecycle behavior, but cannot verify PostgreSQL lock syntax.
    statements = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def begin(self):
            return self

        async def scalar(self, statement):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))

    assert await AssetValidationWorker(Session, object(), settings(tmp_path)).claim() is None
    assert "FOR UPDATE SKIP LOCKED" in statements[0]
