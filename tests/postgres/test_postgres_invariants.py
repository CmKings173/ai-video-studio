from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.api.app.db.models import FinalVideo, Project, User, Video

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
