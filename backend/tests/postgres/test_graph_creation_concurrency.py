"""Product graph-edge creation versus Brand archive, on actual PostgreSQL locks."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.app.api.brands import patch_brand
from apps.api.app.api.products import create_product
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Brand, Product, User
from apps.api.app.schemas.api import BrandPatch, ProductCreate
from tests.postgres.test_dependency_invalidation_concurrency import LockBarrier, _race
from tests.postgres.test_postgres_invariants import DATABASE_URL
from tests.postgres.test_postgres_invariants import pg_engine as pg_engine

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is unset"),
]


class GraphBarrierSession(AsyncSession):
    """Pause after acquiring the real production advisory guard; SQL is unchanged."""

    async def execute(self, statement, *args, **kwargs):
        graph_lock = "pg_advisory_xact_lock" in str(statement)
        if graph_lock:
            barrier, role = self.info["barrier"], self.info["role"]
            await super().execute(text("SET LOCAL lock_timeout = '10s'"))
            await super().execute(text("SET LOCAL statement_timeout = '12s'"))
            barrier.pids[role] = (
                await super().execute(text("SELECT pg_backend_pid()"))
            ).scalar_one()
            if role != barrier.first:
                barrier.attempted.set()
        result = await super().execute(statement, *args, **kwargs)
        if graph_lock and role == barrier.first:
            barrier.acquired.set()
            await barrier.release.wait()
        return result


@pytest.mark.parametrize("first", ["edit", "completion"])
async def test_create_product_vs_archive_brand(pg_engine, first):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    async with factory() as session, session.begin():
        user = User(email=f"graph-{uuid4().hex}@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        brand = Brand(name="Brand", description="", context={}, created_by=user.id)
        session.add(brand)
        await session.flush()
        user_id, brand_id = user.id, brand.id
    barrier = LockBarrier(first)
    created, rejected = [], []

    def guarded(role):
        return async_sessionmaker(
            pg_engine,
            class_=GraphBarrierSession,
            expire_on_commit=False,
            autoflush=False,
            info={"barrier": barrier, "role": role},
        )

    async def create_edge():
        async with guarded("edit")() as session, session.begin():
            try:
                result = await create_product(
                    ProductCreate(name="New product", brand_id=brand_id),
                    await session.get(User, user_id),
                    session,
                )
                created.append(result.id)
            except AppError as error:
                assert error.code == "BRAND_NOT_ACTIVE"
                rejected.append(error.code)

    async def archive():
        async with guarded("completion")() as session, session.begin():
            await patch_brand(
                brand_id,
                BrandPatch(archived=True),
                revision=1,
                user=await session.get(User, user_id),
                session=session,
            )

    await _race(pg_engine, barrier, create_edge, archive)
    async with factory() as session:
        assert (await session.get(Brand, brand_id)).archived
        count = await session.scalar(
            select(func.count()).select_from(Product).where(Product.brand_id == brand_id)
        )
        assert count == (1 if first == "edit" else 0)
        assert bool(created) == (first == "edit")
        assert bool(rejected) == (first == "completion")
