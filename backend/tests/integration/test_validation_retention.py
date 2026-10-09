from datetime import timedelta

import pytest

from apps.api.app.db.models import Asset, utcnow
from apps.api.app.services.asset_retention import AssetRetentionService
from tests.integration.test_asset_validation_worker import DiskStore, enqueue, seed, settings


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["QUEUED", "RUNNING"])
async def test_retention_preserves_old_durable_validation_without_active_lease(
    session_factory, tmp_path, phase
):
    config = settings(tmp_path)
    asset_id, user_id, _ = await seed(session_factory)
    await enqueue(session_factory, config, asset_id, user_id)
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.created_at = utcnow() - timedelta(days=2)
        row.media_metadata = {
            **row.media_metadata,
            "validation": {
                **row.media_metadata["validation"],
                "phase": phase,
            },
        }
    retention = AssetRetentionService(session_factory, DiskStore(asset_id), config)
    assert (await retention.preview()).candidates == []
    assert await retention.claim_batch() == []
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).status == "VALIDATING"
