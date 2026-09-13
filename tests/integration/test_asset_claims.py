from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from apps.api.app.db.models import Asset, Project, User, utcnow
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    REPAIR_CLAIM,
    acquire_claim,
    owns_claim,
)
from apps.api.app.services.asset_retention import AssetRetentionService
from workers.reconciliation import AssetReconciler


def retention_settings(tmp_path):
    return SimpleNamespace(
        pending_asset_retention_hours=1,
        retention_failed_hours=1,
        deleted_asset_retention_hours=1,
        retention_deleting_retry_hours=1,
        retention_batch_size=10,
        asset_operation_claim_timeout_seconds=60,
        workspace_root=tmp_path,
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
