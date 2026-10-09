"""Retirement must recheck history committed while waiting for PostgreSQL locks."""

import asyncio

import pytest
from sqlalchemy import select, text

from apps.api.app.db.models import Scene, SceneGeneration, WorkflowRecord
from apps.api.management.retire_legacy_h3 import retire_legacy_h3
from tests.integration.test_generation_preparation import seed
from tests.postgres.test_asset_validation_concurrency import (
    validation_pg_factory as validation_pg_factory,
)
from tests.postgres.test_postgres_invariants import DATABASE_URL
from tests.postgres.test_postgres_invariants import pg_engine as pg_engine

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.asyncio,
    pytest.mark.skipif(not DATABASE_URL, reason="POSTGRES_TEST_DATABASE_URL is unset"),
]


async def test_cleanup_waits_for_history_writer_and_retains_committed_dependency(
    validation_pg_factory,
):
    factory = validation_pg_factory
    user, scene_id = await seed(factory)
    async with factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        workflow.code = "H3_T2V_STANDARD"
        workflow.workflow = {"1": {"class_type": "HistoricalH3", "inputs": {}}}
        workflow_id = workflow.id
        scene = await session.get(Scene, scene_id)
        video_id = scene.video_id
    async with factory() as writer:
        await writer.begin()
        writer_pid = await writer.scalar(text("select pg_backend_pid()"))
        history = SceneGeneration(
            video_id=video_id,
            scene_id=scene_id,
            workflow_id=workflow_id,
            generation_no=1,
            mode="t2v",
            input_snapshot={"workflow_id": workflow_id},
            created_by=user,
        )
        writer.add(history)
        await writer.flush()  # Holds PostgreSQL's FK KEY SHARE lock until commit.
        started = asyncio.Event()
        cleanup_pid = []

        async def cleanup():
            async with factory() as session, session.begin():
                cleanup_pid.append(await session.scalar(text("select pg_backend_pid()")))
                started.set()
                return await retire_legacy_h3(session, apply=True)

        task = asyncio.create_task(cleanup())
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            # Observe a real lock wait, not an assumed delay or a mocked lock method.
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                async with factory() as observer:
                    blockers = await observer.scalar(
                        text("select pg_blocking_pids(:pid)"), {"pid": cleanup_pid[0]}
                    )
                if writer_pid in blockers:
                    break
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            assert not task.done()
            await writer.commit()
            result = await asyncio.wait_for(task, timeout=10)
        finally:
            await writer.rollback()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    assert result[0]["action"] == "retain_historical_identity_disable"
    assert result[0]["fk_dependencies"]["scene_generations.workflow_id"] == 1
    async with factory() as session:
        workflow = await session.get(WorkflowRecord, workflow_id)
        assert workflow is not None and not workflow.enabled
        assert await session.get(SceneGeneration, history.id) is not None
