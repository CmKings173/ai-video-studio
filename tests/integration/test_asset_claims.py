from __future__ import annotations

import hashlib
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from apps.api.app.db.models import Asset, Project, User, utcnow
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    REPAIR_CLAIM,
    acquire_claim,
    owns_claim,
)
from apps.api.app.services.asset_retention import AssetRetentionService
from apps.api.app.services.asset_service import upload_staging_key
from workers.common import save_output
from workers.reconciliation import AssetReconciler, ReconciliationReport


def retention_settings(tmp_path):
    return SimpleNamespace(
        pending_asset_retention_hours=1,
        retention_failed_hours=1,
        deleted_asset_retention_hours=1,
        retention_deleting_retry_hours=1,
        retention_batch_size=10,
        asset_operation_claim_timeout_seconds=60,
        workspace_root=tmp_path,
        ffprobe_binary="ffprobe",
    )


async def seed_asset(session_factory, *, status="PENDING_UPLOAD", created_at=None):
    async with session_factory() as session, session.begin():
        user = User(
            email=f"claim-{utcnow().timestamp()}@example.test", name="Claim", password_hash="hash"
        )
        session.add(user)
        await session.flush()
        project = Project(name="Claims", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="claim.mp4",
            content_type="video/mp4",
            object_key=f"assets/{project.id}.mp4",
            status=status,
            size_bytes=10,
            created_by=user.id,
            created_at=created_at or utcnow() - timedelta(hours=2),
        )
        session.add(asset)
        await session.flush()
        return asset.id


@pytest.mark.asyncio
async def test_operation_claim_is_exclusive_and_stale_takeover_is_safe(session_factory, tmp_path):
    asset_id = await seed_asset(session_factory)
    first_now = utcnow()
    async with session_factory() as session, session.begin():
        first = await acquire_claim(
            session,
            asset_id,
            REPAIR_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
            now=first_now,
        )
    assert first is not None

    async with session_factory() as session, session.begin():
        assert (
            await acquire_claim(
                session,
                asset_id,
                OUTPUT_WRITE_CLAIM,
                timeout_seconds=60,
                allowed_statuses={"PENDING_UPLOAD"},
                now=first_now + timedelta(seconds=1),
            )
            is None
        )

    async with session_factory() as session, session.begin():
        second = await acquire_claim(
            session,
            asset_id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
            now=first_now + timedelta(seconds=61),
        )
    assert second is not None and second != first

    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        assert owns_claim(asset, second, OUTPUT_WRITE_CLAIM)
        assert not owns_claim(asset, first, REPAIR_CLAIM)


@pytest.mark.asyncio
async def test_retention_excludes_active_operation_claim(session_factory, tmp_path):
    asset_id = await seed_asset(session_factory)
    async with session_factory() as session, session.begin():
        claim_id = await acquire_claim(
            session,
            asset_id,
            REPAIR_CLAIM,
            timeout_seconds=3600,
            allowed_statuses={"PENDING_UPLOAD"},
        )
    assert claim_id
    service = AssetRetentionService(session_factory, object(), retention_settings(tmp_path))
    assert await service.claim_batch(now=utcnow()) == []

    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        asset.operation_claimed_at = utcnow() - timedelta(hours=2)

    claims = await service.claim_batch(now=utcnow())
    assert len(claims) == 1
    assert claims[0].asset_id == asset_id


@pytest.mark.asyncio
async def test_compensation_failure_is_reported(session_factory, tmp_path):
    asset_id = await seed_asset(session_factory)
    async with session_factory() as session, session.begin():
        claim_id = await acquire_claim(
            session,
            asset_id,
            REPAIR_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
        )

    class FailingStore:
        async def delete(self, key):
            raise TimeoutError("temporary object store outage")

    report = SimpleNamespace(compensation_failures=[])
    reconciler = AssetReconciler(session_factory, FailingStore(), retention_settings(tmp_path))
    await reconciler._compensate_promoted(asset_id, claim_id, "assets/promoted.mp4", report)
    assert report.compensation_failures == ["assets/promoted.mp4"]


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), "red").save(buffer, "PNG")
    return buffer.getvalue()


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.put_count = 0

    async def get_bytes(self, key, max_bytes=None):
        return self.objects[key][0]

    async def put_bytes(self, key, data, content_type):
        self.put_count += 1
        self.objects[key] = (data, content_type)

    async def delete(self, key):
        self.objects.pop(key, None)


@pytest.mark.asyncio
async def test_repeated_reconcilers_only_one_promotes_and_finalizes(session_factory, tmp_path):
    data = png_bytes()
    asset_id = await seed_asset(session_factory)
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        asset_key = asset.object_key
    store = MemoryStore()
    store.objects[upload_staging_key(asset_id=asset_id)] = (data, "image/png")
    settings = retention_settings(tmp_path)
    reconciler = AssetReconciler(session_factory, store, settings)
    snapshot = {
        "id": asset_id,
        "status": "PENDING_UPLOAD",
        "object_key": asset_key,
        "checksum": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "content_type": "image/png",
        "filename": "claim.png",
        "created_at": utcnow() - timedelta(hours=2),
    }
    reports = [ReconciliationReport(), ReconciliationReport()]
    await reconciler._repair_pending(snapshot, reports[0])
    await reconciler._repair_pending(snapshot, reports[1])
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.status == "READY"
        assert asset.operation_claim_id is None
    assert store.put_count == 1
    assert sum(bool(report.repaired_assets) for report in reports) == 1


@pytest.mark.asyncio
async def test_repeated_output_collection_is_idempotent(session_factory, tmp_path):
    user_id, project_id = await seed_asset_owner(session_factory)
    store = MemoryStore()
    data = b"deterministic-output"
    metadata = {"kind": "VIDEO", "has_video": True, "width": 16, "height": 9}
    first = await save_output(
        session_factory,
        store,
        owner_id="same-owner",
        role="GENERATED_VIDEO",
        project_id=project_id,
        created_by=user_id,
        data=data,
        metadata=metadata,
    )
    second = await save_output(
        session_factory,
        store,
        owner_id="same-owner",
        role="GENERATED_VIDEO",
        project_id=project_id,
        created_by=user_id,
        data=data,
        metadata=metadata,
    )
    assert first == second
    assert store.put_count == 1


async def seed_asset_owner(session_factory):
    async with session_factory() as session, session.begin():
        user = User(
            email=f"output-{utcnow().timestamp()}@example.test", name="Output", password_hash="hash"
        )
        session.add(user)
        await session.flush()
        project = Project(name="Output", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        return user.id, project.id
