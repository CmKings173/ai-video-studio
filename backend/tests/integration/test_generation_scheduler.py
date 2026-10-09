from datetime import timedelta

import pytest
from sqlalchemy import select

from apps.api.app.db.models import (
    DirectorRun,
    DirectorRunAttempt,
    GenerationAttempt,
    Scene,
    SceneGeneration,
    utcnow,
)
from tests.integration.test_director_dispatcher import seed_run, worker
from workers.director_dispatcher import GenerationScheduler


@pytest.mark.asyncio
async def test_claim_revalidates_stale_route_against_opposite_queue(session_factory, tmp_path):
    run_id, _ = await seed_run(session_factory)
    base = utcnow() - timedelta(hours=1)
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).created_at = base
    waiting = await add_job(session_factory, "standalone", base + timedelta(seconds=1))
    younger = await add_job(session_factory, "director", base + timedelta(seconds=2))
    # The router previously saw the Director head. It disappears before claim.
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).status = "CANCELLED"
    current = scheduler(session_factory, tmp_path)
    assert await current.director.claim() is None
    assert await current.standalone.claim() == waiting
    async with session_factory() as session:
        assert (await session.get(DirectorRun, younger)).status == "CREATED"


@pytest.mark.asyncio
async def test_prior_video_attempt_cannot_override_global_oldest(session_factory, tmp_path):
    from tests.integration.test_generation_races import seed_fairness_queue

    _, _, ids = await seed_fairness_queue(session_factory)
    current = scheduler(session_factory, tmp_path)
    first = await current.standalone.claim()
    assert first == ids[0]
    await current.standalone._finish(first, "FAILED", code="TEST", message="finished")
    async with session_factory() as session, session.begin():
        second = await session.get(SceneGeneration, ids[1])
        between = DirectorRun(
            video_id=second.video_id,
            workflow_id=second.workflow_id,
            task="t2v",
            created_by=second.created_by,
            created_at=second.created_at + timedelta(milliseconds=500),
        )
        session.add(between)
        await session.flush()
        director_id = between.id
    assert await current.standalone.claim() == ids[1]
    async with session_factory() as session:
        assert (await session.get(DirectorRun, director_id)).status == "CREATED"
        assert (await session.get(SceneGeneration, ids[2])).status == "CREATED"


async def add_job(factory, kind, created_at):
    async with factory() as session, session.begin():
        template = await session.scalar(select(DirectorRun))
        if kind == "director":
            job = DirectorRun(
                video_id=template.video_id,
                workflow_id=template.workflow_id,
                task="t2v",
                created_by=template.created_by,
                created_at=created_at,
            )
        else:
            scene = Scene(
                video_id=template.video_id,
                scene_order=await session.scalar(
                    select(Scene.scene_order).order_by(Scene.scene_order.desc()).limit(1)
                )
                + 1,
                prompt="Standalone",
                duration_seconds=5,
            )
            session.add(scene)
            await session.flush()
            job = SceneGeneration(
                video_id=template.video_id,
                scene_id=scene.id,
                workflow_id=template.workflow_id,
                mode="t2v",
                generation_no=1,
                created_by=template.created_by,
                created_at=created_at,
            )
        session.add(job)
        await session.flush()
        return job.id


def scheduler(factory, tmp_path):
    return GenerationScheduler(factory, None, None, None, worker(factory, tmp_path).settings)


@pytest.mark.asyncio
@pytest.mark.parametrize("backlog_kind", ["director", "standalone"])
async def test_continuous_backlog_cannot_starve_other_job_type(
    session_factory, tmp_path, monkeypatch, backlog_kind
):
    run_id, _ = await seed_run(session_factory)
    base = utcnow() - timedelta(hours=1)
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).status = "COMPLETED"
    other_kind = "standalone" if backlog_kind == "director" else "director"
    backlog = [
        await add_job(session_factory, backlog_kind, base + timedelta(seconds=i)) for i in range(3)
    ]
    waiting = await add_job(session_factory, other_kind, base + timedelta(seconds=3))
    processed = []

    async def finish(job_id):
        processed.append(job_id)
        async with session_factory() as session, session.begin():
            job = await session.get(DirectorRun, job_id)
            if job is None:
                job = await session.get(SceneGeneration, job_id)
            job.status = "COMPLETED"
            job.claimed_by = job.lease_expires_at = None

    for _ in range(4):
        # Fresh instances model replicas/restarts; no local turn state survives.
        current = scheduler(session_factory, tmp_path)
        monkeypatch.setattr(current.director, "process", finish)
        monkeypatch.setattr(current.standalone, "process", finish)
        assert await current.run_once()
        await add_job(session_factory, backlog_kind, utcnow())
    assert processed == [*backlog, waiting]


@pytest.mark.asyncio
async def test_oldest_standalone_wins_even_when_director_is_tried_first(
    session_factory, tmp_path, monkeypatch
):
    run_id, _ = await seed_run(session_factory)
    older = await add_job(session_factory, "standalone", utcnow() - timedelta(hours=1))
    current = scheduler(session_factory, tmp_path)
    processed = []

    async def record(job_id):
        processed.append(job_id)

    monkeypatch.setattr(current.director, "process", record)
    monkeypatch.setattr(current.standalone, "process", record)
    assert await current.run_once()
    assert processed == [older]
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "CREATED"
        assert await session.scalar(select(DirectorRunAttempt.id)) is None
        assert (await session.get(SceneGeneration, older)).status == "DISPATCHING"


@pytest.mark.asyncio
@pytest.mark.parametrize("active_kind", ["director", "standalone"])
async def test_active_lease_and_recovery_take_precedence_over_older_pending(
    session_factory, tmp_path, monkeypatch, active_kind
):
    run_id, _ = await seed_run(session_factory)
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).status = "COMPLETED"
    active_id = await add_job(session_factory, active_kind, utcnow())
    current = scheduler(session_factory, tmp_path)
    active_worker = getattr(current, active_kind)
    assert await active_worker.claim() == active_id
    other_kind = "standalone" if active_kind == "director" else "director"
    pending_id = await add_job(session_factory, other_kind, utcnow() - timedelta(hours=1))
    assert not await scheduler(session_factory, tmp_path).run_once()
    model = DirectorRun if active_kind == "director" else SceneGeneration
    attempt_model = DirectorRunAttempt if active_kind == "director" else GenerationAttempt
    async with session_factory() as session, session.begin():
        active = await session.get(model, active_id)
        active.comfy_prompt_id = "existing-prompt"
        active.lease_expires_at = utcnow() - timedelta(seconds=1)
    recovered = scheduler(session_factory, tmp_path)
    processed = []

    async def record(job_id):
        processed.append(job_id)

    monkeypatch.setattr(getattr(recovered, active_kind), "process", record)
    assert await recovered.run_once()
    assert processed == [active_id]
    async with session_factory() as session:
        assert (await session.get(model, active_id)).comfy_prompt_id == "existing-prompt"
        assert len((await session.scalars(select(attempt_model))).all()) == 1
        other_model = SceneGeneration if active_kind == "director" else DirectorRun
        assert (await session.get(other_model, pending_id)).status == "CREATED"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "standalone_id, expected_kind",
    [
        ("00000000-0000-0000-0000-000000000000", "standalone"),
        ("ffffffff-ffff-ffff-ffff-ffffffffffff", "director"),
    ],
)
async def test_equal_creation_times_use_stable_id_order(
    session_factory, tmp_path, monkeypatch, standalone_id, expected_kind
):
    run_id, _ = await seed_run(session_factory)
    timestamp = utcnow()
    pending_id = await add_job(session_factory, "standalone", timestamp)
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).created_at = timestamp
        (await session.get(SceneGeneration, pending_id)).id = standalone_id
    current = scheduler(session_factory, tmp_path)
    processed = []

    async def record(job_id):
        processed.append(job_id)

    monkeypatch.setattr(current.director, "process", record)
    monkeypatch.setattr(current.standalone, "process", record)
    assert await current.run_once()
    assert processed == [standalone_id if expected_kind == "standalone" else run_id]


@pytest.mark.asyncio
async def test_director_members_do_not_supply_standalone_candidates(session_factory, tmp_path):
    run_id, members = await seed_run(session_factory)
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).status = "COMPLETED"
        for member_id in members:
            (await session.get(SceneGeneration, member_id)).created_at = utcnow() - timedelta(
                days=1
            )
    assert not await scheduler(session_factory, tmp_path).run_once()
    async with session_factory() as session:
        assert await session.scalar(select(GenerationAttempt.id)) is None


@pytest.mark.asyncio
async def test_stale_route_does_not_fall_back_to_younger_queue(
    session_factory, tmp_path, monkeypatch
):
    await seed_run(session_factory)
    await add_job(session_factory, "standalone", utcnow() - timedelta(hours=1))
    current = scheduler(session_factory, tmp_path)

    async def lost_candidate():
        return False

    async def unexpected_fallback():
        pytest.fail("A stale routing snapshot must be retried on the next poll")

    monkeypatch.setattr(current.standalone, "run_once", lost_candidate)
    monkeypatch.setattr(current.director, "run_once", unexpected_fallback)
    assert not await current.run_once()
