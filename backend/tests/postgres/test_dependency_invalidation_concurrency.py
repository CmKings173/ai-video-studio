"""Finding 1: real dependency PATCH handlers versus worker completion transactions.

Only PostgreSQL can exercise these row/advisory locks. Each case forces both
Video-lock interleavings and observes the competing backend actually blocked.
No workflow registry activation or external rendering is needed for completion.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.app.api.brands import patch_brand
from apps.api.app.api.products import patch_product
from apps.api.app.core.config import Settings
from apps.api.app.db.models import (
    Asset,
    Brand,
    FinalVideo,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.schemas.api import AssemblyRequest, BrandPatch, ProductPatch
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_freshness import (
    capture_generation_freshness,
    is_generation_fresh,
    is_selected_generation_fresh,
)
from tests.postgres.test_postgres_invariants import DATABASE_URL
from tests.postgres.test_postgres_invariants import pg_engine as pg_engine
from workers.assembler import Assembler
from workers.dispatcher import Dispatcher

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="NOT_RUN: POSTGRES_TEST_DATABASE_URL is unset; PostgreSQL locks require PostgreSQL",
    ),
]

TIMEOUT = 15
OWNER = "dependency-concurrency-test"


@dataclass
class LockBarrier:
    first: str
    acquired: asyncio.Event = field(default_factory=asyncio.Event)
    attempted: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    pids: dict[str, int] = field(default_factory=dict)


class BarrierSession(AsyncSession):
    """Pause after the real first Video lock, without substituting lock SQL.

    The first row lock may be issued through get(), scalar(), or scalars(). The
    second transaction still runs production SQL, including dependency locks.
    An inverse dependency-before-Video order therefore deadlocks/fails the timeout
    when the first transaction resumes; no mocked lock can hide that regression.
    """

    async def _before_video_lock(self):
        if self.info.get("observed_video_lock"):
            return False
        self.info["observed_video_lock"] = True
        barrier = self.info["barrier"]
        role = self.info["role"]
        await super().execute(text("SET LOCAL lock_timeout = '10s'"))
        await super().execute(text("SET LOCAL statement_timeout = '12s'"))
        result = await super().execute(text("SELECT pg_backend_pid()"))
        barrier.pids[role] = result.scalar_one()
        if role != barrier.first:
            barrier.attempted.set()
        return True

    async def _after_video_lock(self, observed):
        barrier = self.info["barrier"]
        if observed and self.info["role"] == barrier.first:
            barrier.acquired.set()
            await barrier.release.wait()

    async def get(self, entity, ident, **kwargs):
        observed = False
        if entity is Video and kwargs.get("with_for_update"):
            observed = await self._before_video_lock()
        result = await super().get(entity, ident, **kwargs)
        await self._after_video_lock(observed)
        return result

    async def scalars(self, statement, *args, **kwargs):
        observed = False
        if getattr(statement, "_for_update_arg", None) is not None and any(
            column.get("entity") is Video
            for column in getattr(statement, "column_descriptions", [])
        ):
            observed = await self._before_video_lock()
        result = await super().scalars(statement, *args, **kwargs)
        await self._after_video_lock(observed)
        return result

    async def scalar(self, statement, *args, **kwargs):
        observed = False
        if getattr(statement, "_for_update_arg", None) is not None and any(
            column.get("entity") is Video
            for column in getattr(statement, "column_descriptions", [])
        ):
            observed = await self._before_video_lock()
        result = await super().scalar(statement, *args, **kwargs)
        await self._after_video_lock(observed)
        return result


async def _race(engine, barrier, edit, completion):
    """A bounded barrier plus pg_blocking_pids proves overlap, not elapsed time."""
    operations = {"edit": edit, "completion": completion}
    second = "completion" if barrier.first == "edit" else "edit"
    tasks = []
    try:
        async with asyncio.timeout(TIMEOUT):
            tasks.append(asyncio.create_task(operations[barrier.first]()))
            await barrier.acquired.wait()
            tasks.append(asyncio.create_task(operations[second]()))
            await barrier.attempted.wait()
            assert barrier.pids[second] != barrier.pids[barrier.first]
            async with engine.connect() as observer:
                while True:
                    # Propagate a premature completion or DB error immediately.
                    for task in tasks:
                        if task.done():
                            task.result()
                            pytest.fail("Transaction finished before the lock barrier was released")
                    blockers = await observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": barrier.pids[second]}
                    )
                    if barrier.pids[barrier.first] in blockers:
                        break
                    await asyncio.sleep(0.01)
            barrier.release.set()
            await asyncio.gather(*tasks)
    finally:
        barrier.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _seed(factory, *, assembly):
    """Unique committed rows; a disabled workflow avoids global registry conflicts."""
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(
            email=f"dependency-race-{suffix}@example.test", name="Editor", password_hash="hash"
        )
        session.add(user)
        await session.flush()
        project = Project(name=f"Dependency race {suffix}", description="", created_by=user.id)
        brand = Brand(name=f"Brand {suffix}", description="", context={}, created_by=user.id)
        workflow = WorkflowRecord(
            code=f"DEPENDENCY_RACE_{suffix}",
            mode="t2v",
            version="1",
            enabled=False,
            workflow={},
            slots={},
            workflow_hash="a" * 64,
            slot_map_hash="b" * 64,
            created_by=user.id,
        )
        session.add_all([project, brand, workflow])
        await session.flush()
        product = Product(
            name=f"Product {suffix}",
            description="",
            context={},
            brand_id=brand.id,
            created_by=user.id,
        )
        session.add(product)
        await session.flush()
        video = Video(
            project_id=project.id,
            product_id=product.id,
            title="Dependency race",
            brief="Bottle launch",
            aspect_ratio="9:16",
            created_by=user.id,
            status="GENERATING",
        )
        session.add(video)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Bottle in warm light")
        output = Asset(
            project_id=project.id,
            kind="VIDEO",
            filename="output.mp4",
            content_type="video/mp4",
            object_key=f"dependency-race/{suffix}/output.mp4",
            status="READY",
            checksum="c" * 64,
            size_bytes=4,
            created_by=user.id,
        )
        session.add_all([scene, output])
        await session.flush()
        generation = SceneGeneration(
            scene_id=scene.id,
            video_id=video.id,
            generation_no=1,
            mode="t2v",
            workflow_id=workflow.id,
            created_by=user.id,
            claimed_by=OWNER,
            status="RUNNING",
            input_snapshot={
                "scene_revision": scene.revision,
                "generation_freshness": await capture_generation_freshness(session, scene, video),
            },
        )
        session.add(generation)
        await session.flush()
        final = None
        frozen = None
        completion_output = output
        if assembly:
            generation.status = "COMPLETED"
            generation.claimed_by = None
            generation.output_asset_id = output.id
            scene.selected_generation_id = generation.id
            await session.flush()
            final = await AssemblyService().create(
                session,
                video_id=video.id,
                request=AssemblyRequest(),
                expected_revision=video.revision,
                user_id=user.id,
                request_id=None,
            )
            final.status, final.claimed_by = "ASSEMBLING", OWNER
            frozen = deepcopy(final.manifest)
            completion_output = Asset(
                project_id=project.id,
                kind="VIDEO",
                filename="final.mp4",
                content_type="video/mp4",
                object_key=f"dependency-race/{suffix}/final.mp4",
                status="READY",
                checksum="d" * 64,
                size_bytes=8,
                created_by=user.id,
            )
            session.add(completion_output)
            await session.flush()
        return {
            "user": user.id,
            "product": product.id,
            "brand": brand.id,
            "video": video.id,
            "scene": scene.id,
            "generation": generation.id,
            "output": completion_output.id,
            "scene_output": output.id,
            "final": final.id if final else None,
            "manifest": frozen,
            "video_revision": video.revision,
            "snapshot": deepcopy(generation.input_snapshot),
        }


async def _exercise(pg_engine, tmp_path, *, dependency, assembly, first):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    ids = await _seed(factory, assembly=assembly)
    barrier = LockBarrier(first)

    def gated_factory(role):
        return async_sessionmaker(
            pg_engine,
            class_=BarrierSession,
            expire_on_commit=False,
            autoflush=False,
            info={"barrier": barrier, "role": role},
        )

    edit_factory = gated_factory("edit")
    worker_factory = gated_factory("completion")
    settings = Settings(_env_file=None, workspace_root=tmp_path)
    worker = (
        Assembler(worker_factory, None, None, settings)
        if assembly
        else Dispatcher(worker_factory, None, None, None, settings)
    )
    worker.owner = OWNER

    async def edit():
        async with edit_factory() as session, session.begin():
            user = await session.get(User, ids["user"])
            if dependency == "product":
                await patch_product(
                    ids["product"],
                    ProductPatch(context={"tone": "changed"}),
                    revision=1,
                    user=user,
                    session=session,
                )
            else:
                await patch_brand(
                    ids["brand"],
                    BrandPatch(context={"tone": "changed"}),
                    revision=1,
                    user=user,
                    session=session,
                )

    async def completion():
        await worker._finish(
            ids["final"] if assembly else ids["generation"],
            "READY" if assembly else "COMPLETED",
            asset_id=ids["output"],
        )

    try:
        await _race(pg_engine, barrier, edit, completion)
        async with factory() as session:
            video = await session.get(Video, ids["video"])
            scene = await session.get(Scene, ids["scene"])
            generation = await session.get(SceneGeneration, ids["generation"])
            output = await session.get(Asset, ids["output"])
            edited = await session.get(
                Product if dependency == "product" else Brand, ids[dependency]
            )
            assert edited.context == {"tone": "changed"}
            assert edited.revision == 2
            assert video.status == "DIRTY"
            assert video.revision == ids["video_revision"] + 1 + (
                not assembly and first == "completion"
            )
            assert generation.status == "COMPLETED"
            assert generation.output_asset_id == ids["scene_output"]
            assert generation.input_snapshot == ids["snapshot"]
            assert generation.claimed_by is None
            assert generation.error_code is None
            assert output.status == "READY" and output.deleted_at is None
            assert (await session.get(Asset, ids["scene_output"])).status == "READY"
            assert not await is_generation_fresh(session, scene, video, generation)
            assert not await is_selected_generation_fresh(session, scene, video)
            if assembly:
                final = await session.get(FinalVideo, ids["final"])
                assert final.status == "READY" and final.output_asset_id == output.id
                assert final.manifest == ids["manifest"]
                assert final.claimed_by is None and final.error_code is None
                assert scene.selected_generation_id == generation.id
                # Completion first promotes a fresh final, then the edit retains
                # its historical pointer. Edit first prevents stale promotion.
                assert video.current_final_video_id == (final.id if first == "completion" else None)
            else:
                assert scene.selected_generation_id == (
                    generation.id if first == "completion" else None
                )
                assert video.current_final_video_id is None
    finally:
        # Match the disposable PG lane convention: retain unique history but
        # leave no runnable jobs, even if a lock regression failed the race.
        async with factory() as session, session.begin():
            generation = await session.get(SceneGeneration, ids["generation"])
            if generation.status != "COMPLETED":
                generation.status, generation.claimed_by = "FAILED", None
            if ids["final"]:
                final = await session.get(FinalVideo, ids["final"])
                if final.status != "READY":
                    final.status, final.claimed_by = "FAILED", None


@pytest.mark.parametrize("first", ["edit", "completion"])
async def test_product_edit_vs_assembly_promotion(pg_engine, tmp_path, first):
    await _exercise(pg_engine, tmp_path, dependency="product", assembly=True, first=first)


@pytest.mark.parametrize("first", ["edit", "completion"])
async def test_brand_edit_vs_assembly_promotion(pg_engine, tmp_path, first):
    # Video.brand_id is null: exercise the inherited Product.brand_id consumer.
    await _exercise(pg_engine, tmp_path, dependency="brand", assembly=True, first=first)


@pytest.mark.parametrize("first", ["edit", "completion"])
async def test_product_edit_vs_generation_completion(pg_engine, tmp_path, first):
    await _exercise(pg_engine, tmp_path, dependency="product", assembly=False, first=first)
