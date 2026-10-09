from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.core.config import Settings
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunAttempt,
    DirectorRunMember,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.integrations.comfy_adapter import ComfyError
from apps.api.app.services.generation_freshness import (
    bind_execution_group_freshness,
    capture_generation_freshness,
)
from tests.integration.test_generation_preparation import seed
from workers.director_dispatcher import DirectorDispatcher
from workers.dispatcher import Dispatcher


async def seed_run(factory):
    user_id, scene_id = await seed(factory)
    async with factory() as session, session.begin():
        first = await session.get(Scene, scene_id)
        workflow = await session.scalar(select(WorkflowRecord))
        second = Scene(
            video_id=first.video_id,
            scene_order=1,
            prompt="Second shot",
            duration_seconds=5,
            spec={"continuity": "CONTINUOUS"},
        )
        session.add(second)
        await session.flush()
        run = DirectorRun(
            video_id=first.video_id, workflow_id=workflow.id, task="t2v", created_by=user_id
        )
        session.add(run)
        await session.flush()
        ids = []
        for index, scene in enumerate((first, second)):
            generation = SceneGeneration(
                video_id=first.video_id,
                scene_id=scene.id,
                workflow_id=workflow.id,
                mode="t2v",
                generation_no=1,
                created_by=user_id,
                input_snapshot={"scene_revision": scene.revision},
            )
            session.add(generation)
            await session.flush()
            ids.append(generation.id)
            session.add(
                DirectorRunMember(
                    director_run_id=run.id,
                    scene_id=scene.id,
                    scene_generation_id=generation.id,
                    member_index=index,
                    continuity="CUT" if index == 0 else "CONTINUOUS",
                )
            )
        await session.flush()
        video = await session.get(Video, first.video_id)
        generations = [await session.get(SceneGeneration, key) for key in ids]
        for scene, generation in zip((first, second), generations, strict=True):
            generation.input_snapshot = {
                **generation.input_snapshot,
                "generation_freshness": await capture_generation_freshness(session, scene, video),
            }
        run.input_snapshot = {
            "members": [
                {"scene_id": generation.scene_id, "scene_generation_id": generation.id}
                for generation in generations
            ]
        }
        bind_execution_group_freshness(generations, run)
        return run.id, ids


def worker(factory, tmp_path, adapter=None):
    return DirectorDispatcher(
        factory,
        adapter,
        None,
        None,
        Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
    )


@pytest.mark.asyncio
async def test_shared_admission_and_reclaim_keep_single_attempt(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)
    director = worker(session_factory, tmp_path)
    assert await director.claim() == run_id
    standalone = Dispatcher(session_factory, None, None, None, director.settings)
    assert await standalone.claim() is None
    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        run.comfy_prompt_id = "aggregate-prompt"
        await director._project_members(session, run, {"status": "QUEUED"})
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
    recovered = worker(session_factory, tmp_path)
    assert await recovered.claim() == run_id
    async with session_factory() as session:
        attempts = list((await session.scalars(select(DirectorRunAttempt))).all())
        assert len(attempts) == 1
        assert (await session.get(DirectorRun, run_id)).comfy_prompt_id == "aggregate-prompt"
        for generation_id in ids:
            assert (await session.get(SceneGeneration, generation_id)).comfy_prompt_id is None


@pytest.mark.asyncio
async def test_progress_updates_all_members_without_run_counter_columns(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)

    async def stream(*args):
        yield {"type": "execution_start"}
        yield {"type": "progress", "current": 3, "total": 8}

    director = worker(session_factory, tmp_path, SimpleNamespace(stream_progress=stream))
    await director.claim()
    await director._progress_listener(run_id, "client", "prompt")
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "RUNNING"
        for generation_id in ids:
            generation = await session.get(SceneGeneration, generation_id)
            assert (generation.progress_current, generation.progress_total) == (3, 8)
            assert generation.status == "RUNNING"


@pytest.mark.asyncio
async def test_cancellation_finalizes_every_member_atomically(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)
    director = worker(session_factory, tmp_path)
    await director.claim()
    async with session_factory() as session, session.begin():
        (await session.get(DirectorRun, run_id)).status = "CANCEL_REQUESTED"
        for generation_id in ids:
            (await session.get(SceneGeneration, generation_id)).status = "CANCEL_REQUESTED"
    await director._finish(run_id, "COMPLETED", artifacts=[])
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "CANCELLED"
        for generation_id in ids:
            assert (await session.get(SceneGeneration, generation_id)).status == "CANCELLED"


@pytest.mark.asyncio
async def test_accepted_director_output_too_large_fails_run_and_releases_admission(
    session_factory, tmp_path
):
    run_id, generation_ids = await seed_run(session_factory)
    async with session_factory() as session, session.begin():
        first_run = await session.get(DirectorRun, run_id)
        next_run = DirectorRun(
            video_id=first_run.video_id,
            workflow_id=first_run.workflow_id,
            task=first_run.task,
            created_by=first_run.created_by,
        )
        session.add(next_run)
        await session.flush()
        next_run_id = next_run.id

    class AcceptedOversizedDirectorDispatcher(DirectorDispatcher):
        async def process(self, claimed_run_id):
            async with self.factory() as session, session.begin():
                run = await session.get(DirectorRun, claimed_run_id, with_for_update=True)
                run.status = run.phase = "COLLECTING"
                run.comfy_prompt_id = "accepted-aggregate-prompt"
                attempt = await session.scalar(
                    select(DirectorRunAttempt)
                    .where(DirectorRunAttempt.director_run_id == claimed_run_id)
                    .order_by(DirectorRunAttempt.attempt_no.desc())
                    .limit(1)
                )
                attempt.status = "COLLECTING"
                await self._project_members(session, run, {"status": "COLLECTING"})
            raise ComfyError("Generated output exceeds size limit", "COMFY_OUTPUT_TOO_LARGE")

    director = AcceptedOversizedDirectorDispatcher(
        session_factory,
        None,
        None,
        None,
        Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
    )
    assert await director.run_once() is True

    async with session_factory() as session:
        run = await session.get(DirectorRun, run_id)
        attempt = await session.scalar(
            select(DirectorRunAttempt)
            .where(DirectorRunAttempt.director_run_id == run_id)
        )
        assert run.status == "FAILED"
        assert run.error_code == "COMFY_OUTPUT_TOO_LARGE"
        assert run.claimed_by is None
        assert run.lease_expires_at is None
        assert attempt.status == "FAILED"
        assert attempt.error_code == "COMFY_OUTPUT_TOO_LARGE"
        for generation_id in generation_ids:
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == "FAILED"
            assert generation.error_code == "COMFY_OUTPUT_TOO_LARGE"
            assert generation.claimed_by is None
            assert generation.lease_expires_at is None

    assert await director.claim() == next_run_id


@pytest.mark.asyncio
async def test_incomplete_artifact_coverage_cannot_complete_any_member(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)
    director = worker(session_factory, tmp_path)
    await director.claim()
    with pytest.raises(ValueError, match="DIRECTOR_SEGMENT_COVERAGE_INVALID"):
        await director._finish(run_id, "COMPLETED", artifacts=[])
    async with session_factory() as session:
        for generation_id in ids:
            assert (await session.get(SceneGeneration, generation_id)).status == "DISPATCHING"


@pytest.mark.asyncio
async def test_success_finalizes_and_selects_every_member_once(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)
    director = worker(session_factory, tmp_path)
    await director.claim()
    outputs = []
    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        for index in range(len(ids)):
            asset = Asset(
                kind="VIDEO",
                role="GENERATED_VIDEO",
                filename=f"member-{index}.mp4",
                content_type="video/mp4",
                object_key=f"review/member-{index}",
                status="READY",
                checksum="a" * 64,
                size_bytes=4,
                created_by=run.created_by,
            )
            session.add(asset)
            await session.flush()
            outputs.append(
                {
                    "role": "segment",
                    "member_index": index,
                    "asset_id": asset.id,
                    "checksum": asset.checksum,
                    "size_bytes": asset.size_bytes,
                    "measured": {"width": 864, "height": 480},
                }
            )
    await director._finish(run_id, "COMPLETED", artifacts=outputs)
    await director._finish(run_id, "FAILED", code="LATE_FAILURE")
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "COMPLETED"
        assert (await session.scalar(select(DirectorRunAttempt))).status == "COMPLETED"
        for index, generation_id in enumerate(ids):
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == "COMPLETED"
            assert generation.output_asset_id == outputs[index]["asset_id"]
            assert generation.output_metadata["width"] == 864
            assert (
                await session.get(Scene, generation.scene_id)
            ).selected_generation_id == generation_id


@pytest.mark.asyncio
async def test_retry_keeps_members_and_terminal_limit(session_factory, tmp_path):
    run_id, ids = await seed_run(session_factory)
    director = worker(session_factory, tmp_path)
    await director.claim()
    await director._retry_or_finish(run_id, "COMFY_EXECUTION_FAILED", "test failure")
    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        assert run.status == "CREATED"
        assert (await session.scalar(select(DirectorRunAttempt))).status == "FAILED"
        for generation_id in ids:
            assert (await session.get(SceneGeneration, generation_id)).status == "CREATED"
        run.attempt_count = director.settings.max_generation_attempts
    assert await director.claim() == run_id
    await director.process(run_id)
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "FAILED"
        for generation_id in ids:
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.status == "FAILED"
            assert generation.error_code == "GENERATION_RETRY_EXHAUSTED"
