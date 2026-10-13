"""Real mutation routes versus Video creation, in both lock interleavings.

No migrations run here: the lead must integrate C's schema and migrate the
approved disposable DB first. A barrier and pg_blocking_pids prove overlap;
all production row/advisory SQL runs unchanged on independent connections.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.app.api.brands import patch_brand
from apps.api.app.api.products import archive_product, patch_product
from apps.api.app.api.projects import archive_project
from apps.api.app.api.videos import create_video
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Brand, Product, Project, Scene, User, Video
from apps.api.app.schemas.api import BrandPatch, ProductPatch, VideoCreate

DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="NOT_RUN: POSTGRES_TEST_DATABASE_URL is unset; PostgreSQL locks require PostgreSQL",
    ),
]
TIMEOUT = 20


@pytest_asyncio.fixture
async def guard_pg_engine():
    assert DATABASE_URL is not None
    url = make_url(DATABASE_URL)
    database = (url.database or "").lower()
    if (
        url.get_backend_name() != "postgresql"
        or database == "studio"
        or not database.startswith(("phase2_", "test_"))
    ):
        pytest.fail("Refusing PostgreSQL target: require a disposable phase2_ or test_ database")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT current_database()")) == url.database
    yield engine
    await engine.dispose()


@dataclass
class Barrier:
    acquired: asyncio.Event = field(default_factory=asyncio.Event)
    attempted: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    pids: dict[str, int] = field(default_factory=dict)


class ObservedSession(AsyncSession):
    def _attempt(self, statement):
        if self.info.get("second") and (
            getattr(statement, "_for_update_arg", None) is not None
            or "pg_advisory_xact_lock" in str(statement)
        ):
            self.info["barrier"].attempted.set()

    async def execute(self, statement, *args, **kwargs):
        self._attempt(statement)
        return await super().execute(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        self._attempt(statement)
        return await super().scalar(statement, *args, **kwargs)

    async def scalars(self, statement, *args, **kwargs):
        self._attempt(statement)
        return await super().scalars(statement, *args, **kwargs)


async def _seed(factory):
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"creation-race-{suffix}@example.test", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Creation race {suffix}", created_by=user.id)
        brand = Brand(name="Actual", context={}, created_by=user.id)
        replacement = Brand(name="Replacement", context={}, created_by=user.id)
        session.add_all([project, brand, replacement])
        await session.flush()
        product = Product(name="Product", context={}, brand_id=brand.id, created_by=user.id)
        session.add(product)
        await session.flush()
        existing = Video(
            project_id=project.id, product_id=product.id, title="Existing", created_by=user.id
        )
        session.add(existing)
        await session.flush()
        return {
            "owner": user.id,
            "project": project.id,
            "product": product.id,
            "brand": brand.id,
            "replacement": replacement.id,
            "existing": existing.id,
        }


async def _cleanup(factory, ids):
    # Only this test's unique resources; never global delete/drop/truncate.
    async with factory() as session, session.begin():
        video_ids = select(Video.id).where(Video.project_id == ids["project"])
        await session.execute(delete(Scene).where(Scene.video_id.in_(video_ids)))
        await session.execute(delete(Video).where(Video.project_id == ids["project"]))
        await session.execute(delete(Product).where(Product.id == ids["product"]))
        await session.execute(delete(Brand).where(Brand.id.in_((ids["brand"], ids["replacement"]))))
        await session.execute(delete(Project).where(Project.id == ids["project"]))
        await session.execute(delete(User).where(User.id == ids["owner"]))


async def _setup(session, barrier, role, first):
    session.info.update(barrier=barrier, second=(role != first))
    await session.execute(text("SET LOCAL lock_timeout = '12s'"))
    await session.execute(text("SET LOCAL statement_timeout = '15s'"))
    barrier.pids[role] = await session.scalar(text("SELECT pg_backend_pid()"))


async def _race(engine, barrier, operations, first):
    second = "create" if first == "edit" else "edit"
    tasks = []
    try:
        async with asyncio.timeout(TIMEOUT):
            tasks.append(asyncio.create_task(operations[first]()))
            await barrier.acquired.wait()
            tasks.append(asyncio.create_task(operations[second]()))
            await barrier.attempted.wait()
            assert barrier.pids[first] != barrier.pids[second]
            async with engine.connect() as observer:
                while True:
                    for task in tasks:
                        if task.done():
                            task.result()
                            pytest.fail("Competing operation completed before releasing held locks")
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": barrier.pids[second]}
                    )
                    if barrier.pids[first] in blockers:
                        break
                    await asyncio.sleep(0.01)
            barrier.release.set()
            results = await asyncio.gather(*tasks)
            return dict(zip((first, second), results, strict=True))
    finally:
        barrier.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("first", ["edit", "create"])
@pytest.mark.parametrize(
    "change",
    ["product_archive", "brand_archive", "project_archive", "reassign", "reassign_archived"],
)
async def test_dependency_mutation_vs_actual_video_creation(guard_pg_engine, first, change):
    factory = async_sessionmaker(
        guard_pg_engine, class_=ObservedSession, expire_on_commit=False, autoflush=False
    )
    ids = await _seed(factory)
    principal = SimpleNamespace(id=ids["owner"], role="EDITOR")
    barrier = Barrier()
    if change == "reassign_archived":
        async with factory() as session, session.begin():
            replacement = await session.get(Brand, ids["replacement"])
            replacement.archived = True

    async def edit():
        async with factory() as session, session.begin():
            await _setup(session, barrier, "edit", first)
            if change == "product_archive":
                await archive_product(ids["product"], 1, principal, session)
            elif change == "brand_archive":
                await patch_brand(ids["brand"], BrandPatch(archived=True), 1, principal, session)
            elif change == "project_archive":
                await archive_project(ids["project"], 1, principal, session)
            else:
                if change == "reassign_archived":
                    # Actual Product route rightly forbids selecting an archived
                    # Brand. Exercise real graph invalidation with this persisted
                    # historical reassignment instead of weakening that route.
                    from apps.api.app.services.generation_dependency_invalidation import (
                        invalidate_product_dependents,
                    )

                    async with invalidate_product_dependents(session, ids["product"]) as product:
                        product.brand_id = ids["replacement"]
                        product.revision += 1
                else:
                    await patch_product(
                        ids["product"],
                        ProductPatch(brand_id=ids["replacement"]),
                        1,
                        principal,
                        session,
                    )
            await session.flush()
            if first == "edit":
                barrier.acquired.set()
                await barrier.release.wait()
        return "committed"

    async def create():
        try:
            async with factory() as session, session.begin():
                # Preload before the competing mutation commits, reproducing
                # stale rows on a genuine independent PostgreSQL connection.
                loaded = [
                    await session.get(model, ids[key])
                    for model, key in ((Project, "project"), (Product, "product"), (Brand, "brand"))
                ]
                assert not any(row.archived for row in loaded)
                await _setup(session, barrier, "create", first)
                result = await create_video(
                    VideoCreate(
                        project_id=ids["project"],
                        product_id=ids["product"],
                        title="Created",
                        kind="QUICK_CLIP",
                        target_duration=5,
                        aspect_ratio="16:9",
                        brief="Race test",
                    ),
                    principal,
                    session,
                )
                assert result.revision == 1
                if first == "edit" and change == "reassign":
                    assert loaded[1].brand_id == ids["replacement"]
                if first == "create":
                    barrier.acquired.set()
                    await barrier.release.wait()
                return result.id
        except AppError as error:
            return error

    try:
        results = await _race(guard_pg_engine, barrier, {"edit": edit, "create": create}, first)
        rejects = first == "edit" and change != "reassign"
        if rejects:
            assert isinstance(results["create"], AppError)
            expected = (
                "PRODUCT"
                if change == "product_archive"
                else "PROJECT"
                if change == "project_archive"
                else "BRAND"
            )
            assert (results["create"].status_code, results["create"].code) == (
                409,
                f"{expected}_NOT_ACTIVE",
            )
        else:
            assert isinstance(results["create"], str)
        async with factory() as session:
            videos = list(
                (
                    await session.scalars(select(Video).where(Video.project_id == ids["project"]))
                ).all()
            )
            assert len(videos) == (1 if rejects else 2)
            scene_count = await session.scalar(
                select(func.count())
                .select_from(Scene)
                .where(Scene.video_id.in_([video.id for video in videos]))
            )
            assert scene_count == (0 if rejects else 1)
            if not rejects:
                created = next(video for video in videos if video.id == results["create"])
                assert created.created_by == ids["owner"]
                # Creation-first commits a valid Video at revision 1. A later
                # Product/Brand edit must discover and invalidate that new
                # consumer, incrementing it exactly once. Project archive does
                # not use semantic dependency invalidation.
                expected_revision = 2 if first == "create" and change != "project_archive" else 1
                assert created.revision == expected_revision
                assert created.brand_id is None  # inherited binding remains inherited
            existing = next(video for video in videos if video.id == ids["existing"])
            assert existing.revision == (1 if change == "project_archive" else 2)
            product = await session.get(Product, ids["product"])
            if change.startswith("reassign"):
                assert product.brand_id == ids["replacement"]
            elif change == "product_archive":
                assert product.archived
            elif change == "brand_archive":
                assert (await session.get(Brand, ids["brand"])).archived
            elif change == "project_archive":
                assert (await session.get(Project, ids["project"])).archived
    finally:
        await _cleanup(factory, ids)
