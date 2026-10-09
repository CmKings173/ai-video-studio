"""Real PostgreSQL lock coverage for retention's durable delete claim."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.app.db.base import Base
from apps.api.app.db.models import Asset, Project, User, utcnow
from apps.api.app.services.asset_retention import AssetRetentionService

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set POSTGRES_TEST_DATABASE_URL to a disposable PostgreSQL database",
    ),
]


@pytest_asyncio.fixture
async def retention_pg_factory():
    assert DATABASE_URL is not None
    admin_engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    schema = "retention_test_" + uuid4().hex
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        DATABASE_URL,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


class PausingDeleteStore:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.deleted: list[str] = []

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.started.set()
        await self.release.wait()


def _settings():
    return SimpleNamespace(
        pending_asset_retention_hours=24,
        retention_failed_hours=24,
        deleted_asset_retention_hours=168,
        retention_deleting_retry_hours=1,
        retention_batch_size=10,
        asset_operation_claim_timeout_seconds=60,
    )


@pytest.mark.asyncio
async def test_concurrent_retention_worker_cannot_take_over_delete_in_flight(
    retention_pg_factory,
):
    factory = retention_pg_factory
    now = utcnow()
    object_key = f"outputs/{uuid4().hex}.mp4"
    async with factory() as session, session.begin():
        user = User(email=f"retention-{uuid4().hex}@example.test", name="PG", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Retention", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="old.mp4",
            content_type="video/mp4",
            object_key=object_key,
            status="DELETED",
            deleted_at=now - timedelta(hours=200),
            size_bytes=1,
            created_by=user.id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    store = PausingDeleteStore()
    service = AssetRetentionService(factory, store, _settings())
    first_task = asyncio.create_task(service.cleanup(now=now))
    try:
        await asyncio.wait_for(store.started.wait(), timeout=5)
        # The first finalizer holds the asset row lock during the bounded
        # external DELETE. PostgreSQL SKIP LOCKED must leave it to that owner.
        second = await asyncio.wait_for(
            service.cleanup(now=now + timedelta(hours=2)), timeout=5
        )
        assert second.candidates == []
        assert second.deleted_objects == 0
        assert store.deleted == [object_key]
    finally:
        store.release.set()

    first = await asyncio.wait_for(first_task, timeout=5)
    assert first.deleted_objects == 1
    assert store.deleted == [object_key]
    async with factory() as session:
        final_asset = await session.get(Asset, asset_id)
        assert final_asset.status == "DELETED"
        assert final_asset.purged_at is not None
