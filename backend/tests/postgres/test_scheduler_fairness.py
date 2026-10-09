import asyncio
from datetime import timedelta

import pytest

from apps.api.app.db.models import DirectorRun, SceneGeneration, utcnow
from tests.integration.test_director_dispatcher import seed_run
from tests.integration.test_generation_scheduler import add_job, scheduler
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


async def test_locked_director_head_does_not_starve_admissible_standalone(
    validation_pg_factory, tmp_path, monkeypatch
):
    factory = validation_pg_factory
    director_id, _ = await seed_run(factory)
    standalone_id = await add_job(factory, "standalone", utcnow() + timedelta(seconds=1))
    current = scheduler(factory, tmp_path)
    processed = []

    async def record(job_id):
        processed.append(job_id)

    monkeypatch.setattr(current.standalone, "process", record)
    async with factory() as editor, editor.begin():
        await editor.get(DirectorRun, director_id, with_for_update=True)
        assert await asyncio.wait_for(current.run_once(), timeout=3)
        assert processed == [standalone_id]
    async with factory() as session:
        assert (await session.get(DirectorRun, director_id)).status == "CREATED"


async def test_replicas_admit_only_one_of_both_pending_job_types(
    validation_pg_factory, tmp_path, monkeypatch
):
    factory = validation_pg_factory
    director_id, _ = await seed_run(factory)
    standalone_id = await add_job(factory, "standalone", utcnow() - timedelta(seconds=1))
    replicas = [scheduler(factory, tmp_path), scheduler(factory, tmp_path)]
    processed = []

    async def record(job_id):
        processed.append(job_id)

    for replica in replicas:
        monkeypatch.setattr(replica.director, "process", record)
        monkeypatch.setattr(replica.standalone, "process", record)
    results = await asyncio.wait_for(
        asyncio.gather(*(replica.run_once() for replica in replicas)), timeout=5
    )
    assert sorted(results) == [False, True]
    assert processed == [standalone_id]
    async with factory() as session:
        assert (await session.get(DirectorRun, director_id)).status == "CREATED"
        assert (await session.get(SceneGeneration, standalone_id)).status == "DISPATCHING"
