"""Real PostgreSQL queue locks in a disposable, isolated test schema."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.app.core.errors import AppError
from apps.api.app.db.base import Base
from apps.api.app.db.models import Asset
from tests.integration.test_asset_validation_worker import enqueue, read, seed, settings
from tests.postgres.test_postgres_invariants import DATABASE_URL
from tests.postgres.test_postgres_invariants import pg_engine as pg_engine
from workers.asset_validation import AssetValidationWorker

pytestmark = [
    pytest.mark.postgres, pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is unset"),
]


@pytest.fixture
async def validation_pg_factory(pg_engine):
    schema = "validation_test_" + uuid4().hex
    async with pg_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        DATABASE_URL, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()
        async with pg_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


async def test_worker_skips_locked_asset_and_only_one_claims(validation_pg_factory, tmp_path):
    factory = validation_pg_factory
    asset_id, user_id, _ = await seed(factory)
    config = settings(tmp_path)
    await enqueue(factory, config, asset_id, user_id)
    acquired, release = asyncio.Event(), asyncio.Event()
    class PausingSession(AsyncSession):
        async def scalar(self, statement, *args, **kwargs):
            result = await super().scalar(statement, *args, **kwargs)
            acquired.set()
            await asyncio.wait_for(release.wait(), timeout=10)
            return result
    first_factory = async_sessionmaker(
        factory.kw["bind"], class_=PausingSession, expire_on_commit=False, autoflush=False,
    )
    first_task = asyncio.create_task(AssetValidationWorker(first_factory, object(), config).claim())
    try:
        await asyncio.wait_for(acquired.wait(), timeout=10)
        assert await asyncio.wait_for(
            AssetValidationWorker(factory, object(), config).claim(), timeout=3
        ) is None
    finally:
        release.set()
    first = await first_task
    assert first and first.asset_id == asset_id
    row = await read(factory, asset_id)
    assert row.operation_claim_id == first.claim_id
    assert row.media_metadata["validation"]["attempts"] == 1


async def test_concurrent_enqueue_binds_first_checksum_under_lock(validation_pg_factory, tmp_path):
    factory = validation_pg_factory
    asset_id, user_id, _ = await seed(factory)
    config = settings(tmp_path)
    acquired, release = asyncio.Event(), asyncio.Event()
    async def first_enqueue():
        from types import SimpleNamespace

        from apps.api.app.services.asset_service import enqueue_asset_validation

        async with factory() as session, session.begin():
            asset = await session.get(Asset, asset_id)
            await enqueue_asset_validation(
                session, config, asset, SimpleNamespace(checksum_sha256="a" * 64), user_id=user_id,
            )
            acquired.set()
            await asyncio.wait_for(release.wait(), timeout=10)
    first_task = asyncio.create_task(first_enqueue())
    await asyncio.wait_for(acquired.wait(), timeout=10)
    second_task = asyncio.create_task(enqueue(factory, config, asset_id, user_id, "b" * 64))
    try:
        # The competing lock cannot observe or replace uncommitted intent.
        await asyncio.sleep(0.1)
        assert not second_task.done()
    finally:
        release.set()
    await first_task
    with pytest.raises(AppError, match="Queued checksum differs"):
        await asyncio.wait_for(second_task, timeout=10)
    row = await read(factory, asset_id)
    assert row.media_metadata["validation"]["expected_checksum"] == "a" * 64
    assert row.media_metadata["validation"]["attempts"] == 0
