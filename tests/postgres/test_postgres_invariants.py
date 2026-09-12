from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.app.db.models import (
    FinalVideo,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from workers.assembler import Assembler
from workers.common import lock_scheduler
from workers.dispatcher import Dispatcher

ROOT = Path(__file__).resolve().parents[2]
DATABASE_URL = os.getenv("POSTGRES_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="set POSTGRES_TEST_DATABASE_URL to run the PostgreSQL production lane",
    ),
]


@pytest.fixture(scope="module")
async def pg_engine():
    assert DATABASE_URL is not None
    env = os.environ.copy()
    env["DATABASE_URL"] = DATABASE_URL
    env["APP_ENV"] = "test"
    await asyncio.to_thread(
        subprocess.run(  # noqa: ASYNC221
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
        )
    )
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_migrations_create_partial_indexes_and_lock_primitives(pg_engine):
    async with pg_engine.connect() as connection:
        indexes = {
            row[0]: row[1]
            for row in (
                await connection.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND indexname IN ("
                        "'uq_workflow_registry_enabled_mode', "
                        "'uq_final_videos_active_video')"
                    )
                )
            ).all()
        }
        assert "WHERE enabled" in indexes["uq_workflow_registry_enabled_mode"]
        assert (
            "status IN ('QUEUED', 'ASSEMBLING', 'CANCEL_REQUESTED')"
            in indexes["uq_final_videos_active_video"]
        )
        await connection.execute(text("SELECT pg_advisory_xact_lock(874321)"))
        await connection.execute(text("SELECT id FROM scene_generations FOR UPDATE SKIP LOCKED"))


@pytest.mark.asyncio
async def test_postgres_immutable_final_manifest_trigger_rejects_mutation(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"pg-{suffix}@example.test", name="PG lane", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"PG {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="PG lane",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="16:9",
            brief="",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="QUEUED",
            manifest={"schema_version": 1},
            manifest_hash="a" * 64,
            assembly_config={},
            created_by=user.id,
        )
        session.add(final)
        await session.flush()
        final_id = final.id

    with pytest.raises(DBAPIError, match="immutable final assembly manifest"):
        async with factory() as session, session.begin():
            row = await session.get(FinalVideo, final_id)
            row.manifest = {"schema_version": 2}
            await session.flush()


async def _seed_dispatch_queue(factory):
    async with factory() as session, session.begin():
        suffix = uuid4().hex
        user = User(email=f"dispatch-{suffix}@example.test", name="PG", password_hash="hash")
        project = Project(name=f"Dispatch {suffix}", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code=f"DISPATCH_{suffix}",
            mode="t2v",
            version="1",
            workflow={"1": {"class_type": "Text", "inputs": {"text": "prompt"}}},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="c" * 64,
            slot_map_hash="d" * 64,
            enabled=True,
            created_by=user.id,
        )
        session.add_all([user, project, workflow])
        await session.flush()
        ids = []
        for index in range(2):
            video = Video(
                project_id=project.id,
                title=f"Video {index}",
                kind="QUICK_CLIP",
                target_duration=5,
                aspect_ratio="16:9",
                brief="brief",
                created_by=user.id,
            )
            session.add(video)
            await session.flush()
            scene = Scene(video_id=video.id, scene_order=0, prompt="prompt", duration_seconds=5)
            session.add(scene)
            await session.flush()
            generation = SceneGeneration(
                video_id=video.id,
                scene_id=scene.id,
                mode="t2v",
                workflow_id=workflow.id,
                generation_no=1,
                operation="ORIGINAL",
                status="CREATED",
                phase="PENDING",
                input_snapshot={"workflow": workflow.workflow, "slots": {}, "assets": []},
                created_by=user.id,
            )
            session.add(generation)
            await session.flush()
            ids.append(generation.id)
        return ids


@pytest.mark.asyncio
async def test_postgres_advisory_scheduler_lock_blocks_second_transaction(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with factory() as session, session.begin():
            await lock_scheduler(session, 874322)
            first_entered.set()
            await release.wait()

    async def waiter():
        await first_entered.wait()
        async with factory() as session, session.begin():
            await lock_scheduler(session, 874322)
            second_entered.set()

    first = asyncio.create_task(holder())
    await first_entered.wait()
    second = asyncio.create_task(waiter())
    await asyncio.sleep(0.1)
    assert not second_entered.is_set()
    release.set()
    await asyncio.gather(first, second)
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_postgres_dispatcher_concurrent_claims_are_unique(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    generation_ids = await _seed_dispatch_queue(factory)
    settings = SimpleNamespace(lease_seconds=30, max_generation_attempts=2)
    first = Dispatcher(factory, None, None, None, settings)
    second = Dispatcher(factory, None, None, None, settings)

    claimed = await asyncio.gather(first.claim(), second.claim())
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0] in generation_ids

    async with factory() as session, session.begin():
        generation = await session.get(SceneGeneration, winners[0], with_for_update=True)
        generation.status = "FAILED"
        generation.claimed_by = None
        generation.lease_expires_at = None

    next_claim = await second.claim()
    assert next_claim in set(generation_ids) - {winners[0]}


async def _seed_assembly_queue(factory):
    async with factory() as session, session.begin():
        suffix = uuid4().hex
        user = User(email=f"assembly-{suffix}@example.test", name="PG", password_hash="hash")
        project = Project(name=f"Assembly {suffix}", description="", created_by=user.id)
        session.add_all([user, project])
        await session.flush()
        ids = []
        for index in range(2):
            video = Video(
                project_id=project.id,
                title=f"Assembly {index}",
                kind="LONG_VIDEO",
                target_duration=30,
                aspect_ratio="16:9",
                brief="brief",
                created_by=user.id,
            )
            session.add(video)
            await session.flush()
            final = FinalVideo(
                video_id=video.id,
                version_no=1,
                status="QUEUED",
                manifest={"scenes": []},
                manifest_hash="e" * 64,
                assembly_config={},
                created_by=user.id,
            )
            session.add(final)
            await session.flush()
            ids.append(final.id)
        return ids


@pytest.mark.asyncio
async def test_postgres_assembler_concurrent_claims_are_unique(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    final_ids = await _seed_assembly_queue(factory)
    settings = SimpleNamespace(lease_seconds=30)
    first = Assembler(factory, None, None, settings)
    second = Assembler(factory, None, None, settings)

    claimed = await asyncio.gather(first.claim(), second.claim())
    winners = [item for item in claimed if item is not None]
    assert len(winners) == 1
    assert winners[0] in final_ids

    async with factory() as session, session.begin():
        final = await session.get(FinalVideo, winners[0], with_for_update=True)
        final.status = "FAILED"
        final.claimed_by = None
        final.lease_expires_at = None

    next_claim = await second.claim()
    assert next_claim in set(final_ids) - {winners[0]}
