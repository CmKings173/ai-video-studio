from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from apps.api.app.api.assembly import cancel_final_version
from apps.api.app.api.brands import patch_brand
from apps.api.app.api.generations import cancel_generation
from apps.api.app.api.products import patch_product
from apps.api.app.api.scenes import patch_scene
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    Brand,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.schemas.api import BrandPatch, GenerationRequest, ProductPatch, ScenePatch
from apps.api.app.services.generation_freshness import (
    capture_generation_freshness,
    is_generation_fresh,
)
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import director_workflow_data
from tests.postgres.test_dependency_invalidation_concurrency import (
    BarrierSession,
    LockBarrier,
    _race,
)
from workers.assembler import Assembler
from workers.director_dispatcher import DirectorDispatcher
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
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
    )
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


async def _seed_scene_and_generation(
    factory, *, generation_status="RUNNING", enable_workflow_for_test=False
):
    suffix = uuid4().hex
    graph, slots, profile, approved = director_workflow_data()
    async with factory() as session, session.begin():
        user = User(email=f"group1-{suffix}@example.test", name="Group 1", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Group 1 {suffix}", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code=f"GROUP1_{suffix}",
            mode="t2v",
            version="1",
            workflow=graph,
            slots=slots,
            required_slots=[],
            profile=profile,
            workflow_hash=approved.workflow_hash,
            slot_map_hash=approved.slot_map_hash,
            enabled=True,
            created_by=user.id,
        )
        session.add_all([project, workflow])
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Group 1 race",
            brief="A test brief",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="9:16",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        scene = Scene(
            video_id=video.id,
            scene_order=0,
            prompt="Original prompt",
            duration_seconds=5,
        )
        session.add(scene)
        await session.flush()
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode="t2v",
            workflow_id=workflow.id,
            generation_no=1,
            operation="ORIGINAL",
            status=generation_status,
            phase="COLLECTING" if generation_status == "RUNNING" else "PENDING",
            revision=4,
            input_snapshot={"schema_version": 1, "assets": []},
            claimed_by="dispatcher-1" if generation_status == "RUNNING" else None,
            created_by=user.id,
        )
        session.add(generation)
        await session.flush()
        if not enable_workflow_for_test:
            workflow.enabled = False
        return user.id, project.id, video.id, scene.id, generation.id, workflow.id


async def _seed_final(factory):
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"final-{suffix}@example.test", name="Group 1", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Final {suffix}", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Assembly race",
            brief="",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            created_by=user.id,
            status="ASSEMBLING",
        )
        session.add(video)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            kind="VIDEO",
            role="ASSEMBLY_OUTPUT",
            filename="final.mp4",
            content_type="video/mp4",
            object_key=f"studio/final/{suffix}.mp4",
            status="READY",
            size_bytes=128,
            checksum="a" * 64,
            created_by=user.id,
        )
        session.add(asset)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="ASSEMBLING",
            manifest={"schema_version": 1, "scenes": []},
            manifest_hash="b" * 64,
            assembly_config={},
            revision=3,
            attempt_count=1,
            claimed_by="assembler-1",
            created_by=user.id,
        )
        session.add(final)
        await session.flush()
        return user.id, project.id, video.id, final.id, asset.id


async def _seed_aggregate_mixed_members(factory):
    suffix = uuid4().hex
    async with factory() as session, session.begin():
        user = User(email=f"aggregate-{suffix}@example.test", name="Group 1", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name=f"Aggregate {suffix}", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code=f"AGGREGATE_{suffix}",
            mode="t2v",
            version="1",
            workflow={"1": {}},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="f" * 64,
            slot_map_hash="1" * 64,
            enabled=False,
            created_by=user.id,
        )
        session.add_all([project, workflow])
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Aggregate cancellation",
            brief="",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            status="GENERATING",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="completed-member.mp4",
            content_type="video/mp4",
            object_key=f"studio/aggregate/{suffix}.mp4",
            status="READY",
            size_bytes=128,
            checksum="2" * 64,
            created_by=user.id,
        )
        session.add(asset)
        await session.flush()
        run = DirectorRun(
            video_id=video.id,
            workflow_id=workflow.id,
            task="t2v",
            status="RUNNING",
            phase="COLLECTING",
            revision=3,
            attempt_count=1,
            input_snapshot={"members": []},
            created_by=user.id,
        )
        session.add(run)
        await session.flush()
        generation_ids = []
        member_ids = []
        for index in range(2):
            scene = Scene(
                video_id=video.id,
                scene_order=index,
                prompt=f"Scene {index}",
                duration_seconds=5,
            )
            session.add(scene)
            await session.flush()
            generation = SceneGeneration(
                video_id=video.id,
                scene_id=scene.id,
                mode="t2v",
                workflow_id=workflow.id,
                generation_no=1,
                operation="ORIGINAL",
                status="RUNNING",
                phase="COLLECTING",
                revision=4,
                input_snapshot={"scene_revision": scene.revision},
                claimed_by="director-1",
                created_by=user.id,
            )
            session.add(generation)
            await session.flush()
            member = DirectorRunMember(
                director_run_id=run.id,
                scene_id=scene.id,
                scene_generation_id=generation.id,
                member_index=index,
                continuity="CUT",
                status="RUNNING",
            )
            session.add(member)
            await session.flush()
            generation_ids.append(generation.id)
            member_ids.append(member.id)
        run.input_snapshot = {
            "members": [
                {"scene_id": index, "scene_generation_id": generation_id}
                for index, generation_id in enumerate(generation_ids)
            ]
        }
        return (
            user.id,
            video.id,
            run.id,
            generation_ids,
            member_ids,
            asset.id,
        )


async def _publish_final(factory, video_id, final_id, asset_id):
    async with factory() as session, session.begin():
        video = await session.get(Video, video_id, with_for_update=True)
        final = await session.get(FinalVideo, final_id, with_for_update=True)
        final.status = "READY"
        final.phase = "READY"
        final.output_asset_id = asset_id
        final.finished_at = utcnow()
        final.revision += 1
        final.claimed_by = None
        final.lease_expires_at = None
        video.status = "READY"
        video.current_final_video_id = final.id


def _interleave_before_video_lock(session: AsyncSession, callback):
    original_scalar = session.scalar
    fired = False

    async def scalar(statement, *args, **kwargs):
        nonlocal fired
        descriptions = getattr(statement, "column_descriptions", ())
        if (
            not fired
            and getattr(statement, "_for_update_arg", None) is not None
            and descriptions
            and descriptions[0].get("entity") is Video
        ):
            fired = True
            await callback()
        return await original_scalar(statement, *args, **kwargs)

    session.scalar = scalar


class _InterleavingFactory:
    def __init__(self, factory, callback):
        self.factory = factory
        self.callback = callback
        self.armed = True

    def __call__(self):
        session = self.factory()
        original_scalar = session.scalar
        original_get = session.get

        async def get(entity, ident, *args, **kwargs):
            if self.armed and entity is Video and kwargs.get("with_for_update"):
                self.armed = False
                await self.callback()
            return await original_get(entity, ident, *args, **kwargs)

        async def scalar(statement, *args, **kwargs):
            descriptions = getattr(statement, "column_descriptions", ())
            if (
                self.armed
                and getattr(statement, "_for_update_arg", None) is not None
                and descriptions
                and descriptions[0].get("entity") is Video
            ):
                self.armed = False
                await self.callback()
            return await original_scalar(statement, *args, **kwargs)

        session.get = get
        session.scalar = scalar
        return session


async def _assert_final_ready(factory, video_id, final_id, asset_id):
    async with factory() as session:
        final = await session.get(FinalVideo, final_id)
        video = await session.get(Video, video_id)
        assert final.status == "READY"
        assert final.output_asset_id == asset_id
        assert final.finished_at is not None
        assert final.revision == 4
        assert final.claimed_by is None
        assert video.status == "READY"
        assert video.current_final_video_id == final_id


@pytest.mark.asyncio
async def test_postgres_scene_patch_rejects_a_stale_prefetched_scene(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        _project_id,
        video_id,
        scene_id,
        _generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory, generation_status="CREATED")

    async def concurrent_edit():
        async with factory() as writer, writer.begin():
            video = await writer.get(Video, video_id, with_for_update=True)
            scene = await writer.get(Scene, scene_id, with_for_update=True)
            scene.prompt = "Committed by concurrent writer"
            scene.revision += 1
            video.revision += 1

    async with factory() as session:
        _interleave_before_video_lock(session, concurrent_edit)
        with pytest.raises(AppError) as raised:
            async with session.begin():
                await patch_scene(
                    scene_id,
                    ScenePatch(prompt="Stale request overwrote the winner"),
                    revision=1,
                    user=await session.get(User, user_id),
                    session=session,
                )
        assert raised.value.status_code == 412

    async with factory() as session:
        scene = await session.get(Scene, scene_id)
        assert scene.prompt == "Committed by concurrent writer"
        assert scene.revision == 2


@pytest.mark.asyncio
async def test_postgres_generation_snapshot_uses_the_refreshed_scene(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        _project_id,
        video_id,
        scene_id,
        _generation_id,
        workflow_id,
    ) = await _seed_scene_and_generation(
        factory, generation_status="CREATED", enable_workflow_for_test=True
    )

    async def concurrent_edit():
        async with factory() as writer, writer.begin():
            video = await writer.get(Video, video_id, with_for_update=True)
            scene = await writer.get(Scene, scene_id, with_for_update=True)
            scene.prompt = "Fresh prompt for the generation snapshot"
            scene.negative_prompt = "Fresh negative prompt"
            scene.revision += 1
            video.revision += 1

    try:
        async with factory() as session, session.begin():
            _interleave_before_video_lock(session, concurrent_edit)
            generation = await GenerationService(
                Settings(_env_file=None, min_free_disk_bytes=0)
            ).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(seed=42),
                user_id=user_id,
                request_id=None,
            )
            snapshot = dict(generation.input_snapshot)
            (await session.get(WorkflowRecord, workflow_id)).enabled = False
    finally:
        async with factory() as cleanup, cleanup.begin():
            workflow = await cleanup.get(WorkflowRecord, workflow_id)
            if workflow is not None:
                workflow.enabled = False
    assert snapshot["scene_revision"] == 2
    assert snapshot["video_revision"] == 2
    assert snapshot["negative_prompt"] == "Fresh negative prompt"
    assert "Fresh prompt for the generation snapshot" in snapshot["prompt"]


@pytest.mark.asyncio
async def test_postgres_cancel_generation_preserves_a_concurrent_completion(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        project_id,
        video_id,
        _scene_id,
        generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory)
    async with factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATION_OUTPUT",
            filename="generated.mp4",
            content_type="video/mp4",
            object_key=f"studio/generation/{uuid4().hex}.mp4",
            status="READY",
            size_bytes=256,
            checksum="c" * 64,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    async def concurrent_completion():
        async with factory() as writer, writer.begin():
            await writer.get(Video, video_id, with_for_update=True)
            generation = await writer.get(SceneGeneration, generation_id, with_for_update=True)
            generation.status = "COMPLETED"
            generation.phase = "COMPLETED"
            generation.output_asset_id = asset_id
            generation.finished_at = utcnow()
            generation.progress_updated_at = generation.finished_at
            generation.revision += 1
            generation.claimed_by = None
            generation.lease_expires_at = None

    async with factory() as session:
        _interleave_before_video_lock(session, concurrent_completion)
        async with session.begin():
            result = await cancel_generation(
                generation_id, await session.get(User, user_id), session
            )
        assert result.status == "COMPLETED"
        assert result.output_asset_id == asset_id
        assert result.revision == 5

    async with factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "COMPLETED"
        assert generation.output_asset_id == asset_id
        assert generation.revision == 5
        assert generation.finished_at is not None


@pytest.mark.asyncio
async def test_postgres_cancel_created_generation_before_claim(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        _project_id,
        _video_id,
        _scene_id,
        generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory, generation_status="CREATED")

    async with factory() as session, session.begin():
        result = await cancel_generation(generation_id, await session.get(User, user_id), session)
        assert result.status == "CANCELLED"

    async with factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCELLED"
        assert generation.finished_at is not None
        assert generation.claimed_by is None
        assert generation.lease_expires_at is None


@pytest.mark.asyncio
async def test_postgres_concurrent_generation_cancels_are_serialized(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        _project_id,
        _video_id,
        _scene_id,
        generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory)

    async def cancel_once():
        async with factory() as session, session.begin():
            return await cancel_generation(generation_id, await session.get(User, user_id), session)

    results = await asyncio.gather(cancel_once(), cancel_once())
    assert [result.status for result in results] == [
        "CANCEL_REQUESTED",
        "CANCEL_REQUESTED",
    ]
    async with factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCEL_REQUESTED"
        assert generation.revision == 6
        assert generation.claimed_by == "dispatcher-1"


@pytest.mark.asyncio
async def test_postgres_expired_lease_recovery_finishes_pending_cancel(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        _user_id,
        _project_id,
        _video_id,
        _scene_id,
        generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory)
    async with factory() as session, session.begin():
        # This module reuses a disposable PostgreSQL database across tests.
        # Clear leftover aggregate leases so this standalone recovery case owns admission.
        await session.execute(
            update(DirectorRun)
            .where(
                DirectorRun.status.in_(
                    {"DISPATCHING", "QUEUED", "RUNNING", "COLLECTING", "CANCEL_REQUESTED"}
                )
            )
            .values(status="CANCELLED", phase="CANCELLED", claimed_by=None, lease_expires_at=None)
        )
        await session.execute(
            update(SceneGeneration)
            .where(
                SceneGeneration.id != generation_id,
                SceneGeneration.status.in_(
                    {"DISPATCHING", "QUEUED", "RUNNING", "COLLECTING", "CANCEL_REQUESTED"}
                ),
            )
            .values(status="CANCELLED", phase="CANCELLED", claimed_by=None, lease_expires_at=None)
        )
        generation = await session.get(SceneGeneration, generation_id)
        generation.status = "CANCEL_REQUESTED"
        generation.phase = "CANCELLING"
        generation.claimed_by = "dispatcher-crashed"
        generation.lease_expires_at = utcnow() - timedelta(minutes=1)
        generation.created_at = utcnow() - timedelta(days=36500)

    worker = Dispatcher(
        factory,
        None,
        None,
        None,
        SimpleNamespace(max_generation_attempts=2, lease_seconds=30),
    )
    claimed_id = await worker.claim()
    assert claimed_id == generation_id
    async with factory() as session:
        recovered = await session.get(SceneGeneration, generation_id)
        assert recovered.status == "CANCEL_REQUESTED"
        assert recovered.claimed_by == worker.owner
        assert recovered.lease_expires_at is not None

    await worker._retry_or_finish(generation_id, "TRANSPORT_INTERRUPTED", "interrupted")

    async with factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCELLED"
        assert generation.finished_at is not None
        assert generation.claimed_by is None
        assert generation.lease_expires_at is None


@pytest.mark.asyncio
async def test_postgres_generation_worker_honors_cancel_committed_before_parent_lock(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        project_id,
        video_id,
        _scene_id,
        generation_id,
        _workflow_id,
    ) = await _seed_scene_and_generation(factory)
    async with factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="completed-during-cancel.mp4",
            content_type="video/mp4",
            object_key=f"studio/generation/{uuid4().hex}.mp4",
            status="READY",
            size_bytes=256,
            checksum="e" * 64,
            created_by=user_id,
            media_metadata={"width": 16, "height": 9, "duration_seconds": 5},
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    async def concurrent_cancel():
        async with factory() as writer, writer.begin():
            await writer.get(Video, video_id, with_for_update=True)
            generation = await writer.get(SceneGeneration, generation_id, with_for_update=True)
            generation.status = "CANCEL_REQUESTED"
            generation.phase = "CANCELLING"
            generation.revision += 1
            generation.progress_updated_at = utcnow()

    worker_factory = _InterleavingFactory(factory, concurrent_cancel)
    worker = Dispatcher(
        worker_factory,
        None,
        None,
        None,
        SimpleNamespace(max_generation_attempts=2),
    )
    worker.owner = "dispatcher-1"
    await worker._finish(generation_id, "COMPLETED", asset_id=asset_id)

    assert not worker_factory.armed
    async with factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCELLED"
        assert generation.output_asset_id is None
        assert generation.revision == 6
        assert generation.claimed_by is None
        assert generation.lease_expires_at is None


@pytest.mark.asyncio
async def test_postgres_aggregate_cancel_preserves_completed_member_after_refresh(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        video_id,
        run_id,
        generation_ids,
        member_ids,
        asset_id,
    ) = await _seed_aggregate_mixed_members(factory)
    completed_generation_id, active_generation_id = generation_ids
    completed_member_id, _active_member_id = member_ids

    async def concurrent_member_completion():
        async with factory() as writer, writer.begin():
            await writer.get(Video, video_id, with_for_update=True)
            await writer.get(DirectorRun, run_id, with_for_update=True)
            member = await writer.get(DirectorRunMember, completed_member_id, with_for_update=True)
            generation = await writer.get(
                SceneGeneration, completed_generation_id, with_for_update=True
            )
            member.status = "COMPLETED"
            member.output_asset_id = asset_id
            generation.status = "COMPLETED"
            generation.phase = "COMPLETED"
            generation.output_asset_id = asset_id
            generation.finished_at = utcnow()
            generation.revision += 1
            generation.claimed_by = None

    async with factory() as session:
        async with session.begin():
            await session.get(DirectorRun, run_id)
            await session.get(SceneGeneration, completed_generation_id)
            await session.get(SceneGeneration, active_generation_id)
            await session.get(DirectorRunMember, completed_member_id)
            await session.get(DirectorRunMember, member_ids[1])
            _interleave_before_video_lock(session, concurrent_member_completion)
            result = await cancel_generation(
                active_generation_id, await session.get(User, user_id), session
            )
        assert result.status == "CANCEL_REQUESTED"

    async with factory() as session:
        run = await session.get(DirectorRun, run_id)
        completed_generation = await session.get(SceneGeneration, completed_generation_id)
        active_generation = await session.get(SceneGeneration, active_generation_id)
        completed_member = await session.get(DirectorRunMember, completed_member_id)
        assert run.status == "CANCEL_REQUESTED"
        assert completed_generation.status == "COMPLETED"
        assert completed_generation.output_asset_id == asset_id
        assert completed_generation.finished_at is not None
        assert completed_generation.revision == 5
        assert completed_member.status == "COMPLETED"
        assert completed_member.output_asset_id == asset_id
        assert active_generation.status == "CANCEL_REQUESTED"


@pytest.mark.asyncio
async def test_postgres_director_worker_honors_aggregate_cancel_before_parent_lock(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    (
        user_id,
        video_id,
        run_id,
        generation_ids,
        member_ids,
        asset_id,
    ) = await _seed_aggregate_mixed_members(factory)
    async with factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        run.claimed_by = "director-1"
        completed_generation = await session.get(SceneGeneration, generation_ids[0])
        completed_generation.status = completed_generation.phase = "COMPLETED"
        completed_generation.output_asset_id = asset_id
        completed_generation.finished_at = utcnow()
        completed_generation.claimed_by = completed_generation.lease_expires_at = None
        completed_generation.revision += 1
        completed_member = await session.get(DirectorRunMember, member_ids[0])
        completed_member.status = "COMPLETED"
        completed_member.output_asset_id = asset_id

    async def concurrent_cancel():
        async with factory() as writer, writer.begin():
            await cancel_generation(generation_ids[1], await writer.get(User, user_id), writer)

    worker_factory = _InterleavingFactory(factory, concurrent_cancel)
    worker = DirectorDispatcher(
        worker_factory,
        None,
        None,
        None,
        SimpleNamespace(max_generation_attempts=2),
    )
    worker.owner = "director-1"
    artifacts = [
        {
            "role": "segment",
            "member_index": index,
            "asset_id": asset_id,
            "size_bytes": 128,
            "checksum": "2" * 64,
            "measured": {},
        }
        for index in range(len(generation_ids))
    ]
    await worker._finish(run_id, "COMPLETED", artifacts=artifacts)

    assert not worker_factory.armed
    async with factory() as session:
        run = await session.get(DirectorRun, run_id)
        completed_generation = await session.get(SceneGeneration, generation_ids[0])
        active_generation = await session.get(SceneGeneration, generation_ids[1])
        completed_member = await session.get(DirectorRunMember, member_ids[0])
        assert run.status == "CANCELLED"
        assert completed_generation.status == "COMPLETED"
        assert completed_generation.output_asset_id == asset_id
        assert completed_member.status == "COMPLETED"
        assert completed_member.output_asset_id == asset_id
        assert active_generation.status == "CANCELLED"


@pytest.mark.asyncio
async def test_postgres_cancel_final_preserves_a_concurrent_ready_publication(pg_engine):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    user_id, _project_id, video_id, final_id, asset_id = await _seed_final(factory)

    async def concurrent_publication():
        await _publish_final(factory, video_id, final_id, asset_id)

    async with factory() as session:
        _interleave_before_video_lock(session, concurrent_publication)
        async with session.begin():
            result = await cancel_final_version(final_id, await session.get(User, user_id), session)
        assert result.status == "READY"
        assert result.output_asset_id == asset_id
        assert result.revision == 4
    await _assert_final_ready(factory, video_id, final_id, asset_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_path", ["finish", "retry"])
async def test_postgres_assembler_does_not_overwrite_concurrent_ready_final(pg_engine, worker_path):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    _user_id, _project_id, video_id, final_id, asset_id = await _seed_final(factory)

    async def concurrent_publication():
        await _publish_final(factory, video_id, final_id, asset_id)

    worker_factory = _InterleavingFactory(factory, concurrent_publication)
    worker = Assembler(
        worker_factory,
        None,
        None,
        SimpleNamespace(lease_seconds=30, max_assembly_attempts=2),
    )
    worker.owner = "assembler-1"
    if worker_path == "finish":
        await worker._finish(final_id, "FAILED", code="LATE_FAILURE", message="late result")
    else:
        await worker._retry_or_finish(final_id, "LATE_FAILURE", "late retry")

    assert not worker_factory.armed
    await _assert_final_ready(factory, video_id, final_id, asset_id)


async def _seed_identity_dependencies(factory, user_id, video_id, *, bind=True):
    async with factory() as session, session.begin():
        brand = Brand(name="Identity brand", context={"tone": "original"}, created_by=user_id)
        session.add(brand)
        await session.flush()
        product = Product(
            name="Identity product",
            context={"feature": "original"},
            brand_id=brand.id,
            created_by=user_id,
        )
        session.add(product)
        await session.flush()
        if bind:
            (await session.get(Video, video_id)).product_id = product.id
        return product.id, brand.id


async def _disable_identity_workflow(factory, workflow_id):
    async with factory() as session, session.begin():
        (await session.get(WorkflowRecord, workflow_id)).enabled = False


@pytest.mark.asyncio
@pytest.mark.parametrize("direct_brand", [False, True])
async def test_postgres_dirty_identity_snapshot_matches_committed_state(pg_engine, direct_brand):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    user_id, _, video_id, scene_id, _, workflow_id = await _seed_scene_and_generation(
        factory,
        generation_status="CREATED",
        enable_workflow_for_test=True,
    )
    product_id, brand_id = await _seed_identity_dependencies(factory, user_id, video_id, bind=False)
    try:
        async with factory() as session, session.begin():
            video = await session.get(Video, video_id)
            scene = await session.get(Scene, scene_id)
            product = await session.get(Product, product_id)
            brand = await session.get(Brand, brand_id)
            video.product_id = product_id
            if direct_brand:
                video.brand_id = brand_id
            video.config = {"music": "same transaction music"}
            video.revision += 1
            scene.prompt = "Same transaction prompt"
            scene.revision += 1
            product.context = {"feature": "same transaction feature"}
            product.revision += 1
            brand.context = {"tone": "same transaction tone"}
            brand.revision += 1
            assert all(row in session.dirty for row in (video, scene, product, brand))
            assert all(session.is_modified(row) for row in (video, scene, product, brand))
            generation = await GenerationService(
                Settings(_env_file=None, min_free_disk_bytes=0)
            ).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(seed=42),
                user_id=user_id,
                request_id=None,
            )
            generation_id = generation.id
            snapshot = dict(generation.input_snapshot)
        async with factory() as observer:
            video = await observer.get(Video, video_id)
            scene = await observer.get(Scene, scene_id)
            product = await observer.get(Product, product_id)
            brand = await observer.get(Brand, brand_id)
            generation = await observer.get(SceneGeneration, generation_id)
            assert video.product_id == product_id
            assert video.brand_id == (brand_id if direct_brand else None)
            assert video.config == {"music": "same transaction music"}
            assert video.revision == scene.revision == 2
            assert scene.prompt == "Same transaction prompt"
            assert product.context == {"feature": "same transaction feature"}
            assert brand.context == {"tone": "same transaction tone"}
            assert product.revision == brand.revision == 2
            assert generation.input_snapshot == snapshot
            assert snapshot["source_scope"]["product_id"] == product_id
            assert snapshot["scene_revision"] == scene.revision
            assert snapshot["video_revision"] == video.revision
            assert scene.prompt in snapshot["prompt"]
            assert snapshot["generation_freshness"] == await capture_generation_freshness(
                observer,
                scene,
                video,
            )
    finally:
        await _disable_identity_workflow(factory, workflow_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [Video, Scene], ids=["video", "scene"])
@pytest.mark.parametrize("advance_revision", [True, False], ids=["revision", "changed-field"])
async def test_postgres_stale_dirty_identity_rejected_without_dml(
    pg_engine,
    model,
    advance_revision,
):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    (
        user_id,
        _,
        video_id,
        scene_id,
        old_generation_id,
        workflow_id,
    ) = await _seed_scene_and_generation(
        factory,
        generation_status="CREATED",
        enable_workflow_for_test=True,
    )
    identity = video_id if model is Video else scene_id
    field = "config" if model is Video else "prompt"
    winner_value = {"music": "winner"} if model is Video else "Winner prompt"
    loser_value = {"music": "stale loser"} if model is Video else "Stale loser prompt"
    statements = []

    def record_sql(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    try:
        async with factory() as loser:
            stale = await loser.get(model, identity)
            setattr(stale, field, loser_value)
            stale.revision += 1
            assert stale in loser.dirty and loser.is_modified(stale)
            # Commit the winner while the losing session retains dirty ORM history.
            async with factory() as winner, winner.begin():
                await winner.get(Video, video_id, with_for_update=True)
                row = await winner.get(model, identity, with_for_update=True)
                setattr(row, field, winner_value)
                if advance_revision:
                    row.revision += 1
            connection = (await loser.connection()).sync_connection
            event.listen(connection, "before_cursor_execute", record_sql)
            try:
                with pytest.raises(AppError) as raised:
                    await GenerationService(Settings(_env_file=None, min_free_disk_bytes=0)).create(
                        loser,
                        scene_id=scene_id,
                        request=GenerationRequest(seed=42),
                        user_id=user_id,
                        request_id=None,
                    )
                assert raised.value.status_code == 412
                assert not [
                    sql
                    for sql in statements
                    if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                ], statements
            finally:
                event.remove(connection, "before_cursor_execute", record_sql)
                await loser.rollback()
        async with factory() as observer:
            row = await observer.get(model, identity)
            assert getattr(row, field) == winner_value
            assert row.revision == (2 if advance_revision else 1)
            generations = list(
                (
                    await observer.scalars(
                        select(SceneGeneration).where(SceneGeneration.scene_id == scene_id)
                    )
                ).all()
            )
            assert [generation.id for generation in generations] == [old_generation_id]
            assert generations[0].status == "CREATED"
    finally:
        await _disable_identity_workflow(factory, workflow_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
@pytest.mark.parametrize("first", ["edit", "completion"], ids=["edit-first", "create-first"])
async def test_postgres_dependency_edit_vs_identity_generation_creation(
    pg_engine,
    dependency,
    first,
):
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    user_id, _, video_id, scene_id, _, workflow_id = await _seed_scene_and_generation(
        factory,
        generation_status="CREATED",
        enable_workflow_for_test=True,
    )
    product_id, brand_id = await _seed_identity_dependencies(factory, user_id, video_id)
    barrier = LockBarrier(first=first)
    competing = async_sessionmaker(
        pg_engine,
        class_=BarrierSession,
        expire_on_commit=False,
        autoflush=False,
    )
    generated = []
    lock_tables = []

    async def edit():
        async with competing(info={"barrier": barrier, "role": "edit"}) as session:
            async with session.begin():
                user = await session.get(User, user_id)
                if dependency == "product":
                    await patch_product(
                        product_id,
                        ProductPatch(context={"feature": "winner"}),
                        revision=1,
                        user=user,
                        session=session,
                    )
                else:
                    await patch_brand(
                        brand_id,
                        BrandPatch(context={"tone": "winner"}),
                        revision=1,
                        user=user,
                        session=session,
                    )

    async def create():
        async with competing(info={"barrier": barrier, "role": "completion"}) as session:
            async with session.begin():
                # Keep clean prefetched identities alive across the competing edit.
                prefetched = [
                    await session.get(model, identity)
                    for model, identity in (
                        (Video, video_id),
                        (Product, product_id),
                        (Brand, brand_id),
                        (Scene, scene_id),
                    )
                ]
                connection = (await session.connection()).sync_connection

                def record_locks(_connection, _cursor, sql, _parameters, _context, _many):
                    if "FOR UPDATE" in sql.upper():
                        for table in ("videos", "products", "brands", "scenes"):
                            if f"FROM {table} " in sql or f"FROM {table}\n" in sql:
                                lock_tables.append(table)

                event.listen(connection, "before_cursor_execute", record_locks)
                try:
                    generation = await GenerationService(
                        Settings(_env_file=None, min_free_disk_bytes=0)
                    ).create(
                        session,
                        scene_id=scene_id,
                        request=GenerationRequest(seed=42, workflow_id=workflow_id),
                        user_id=user_id,
                        request_id=None,
                    )
                    generated.append(generation.id)
                    assert all(row in session for row in prefetched)
                finally:
                    event.remove(connection, "before_cursor_execute", record_locks)

    try:
        await _race(pg_engine, barrier, edit, create)
        first_locks = list(dict.fromkeys(lock_tables))
        assert first_locks == ["videos", "products", "brands", "scenes"]
        async with factory() as observer:
            video = await observer.get(Video, video_id)
            scene = await observer.get(Scene, scene_id)
            generation = await observer.get(SceneGeneration, generated[0])
            model, identity = (
                (Product, product_id) if dependency == "product" else (Brand, brand_id)
            )
            row = await observer.get(model, identity)
            assert row.revision == 2
            assert row.context == (
                {"feature": "winner"} if dependency == "product" else {"tone": "winner"}
            )
            assert video.revision == 2
            assert scene.revision == 1
            saved = generation.input_snapshot["generation_freshness"]
            current = await capture_generation_freshness(observer, scene, video)
            assert saved["inputs"]["dependencies"][dependency]["context"] == (
                row.context
                if first == "edit"
                else ({"feature": "original"} if dependency == "product" else {"tone": "original"})
            )
            assert (saved == current) is (first == "edit")
            assert await is_generation_fresh(observer, scene, video, generation) is (
                first == "edit"
            )
    finally:
        await _disable_identity_workflow(factory, workflow_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["product", "brand"])
async def test_postgres_dirty_dependency_binding_rejects_busy_graph_without_dml(
    pg_engine,
    dependency,
):
    """A graph editor must not miss a consumer bound after its consumer SELECT.

    The editor holds the real advisory guard, with an empty locked consumer set.
    A dirty binding must fail promptly, even if its caller already owns Video.
    Waiting for the graph guard would invert graph -> Video lock ordering.
    """
    factory = async_sessionmaker(pg_engine, expire_on_commit=False, autoflush=False)
    (
        user_id,
        _,
        video_id,
        scene_id,
        old_generation_id,
        workflow_id,
    ) = await _seed_scene_and_generation(
        factory,
        generation_status="CREATED",
        enable_workflow_for_test=True,
    )
    product_id, brand_id = await _seed_identity_dependencies(factory, user_id, video_id, bind=False)
    resolved = asyncio.Event()
    release = asyncio.Event()
    statements = []

    async def edit():
        async with factory() as session, session.begin():
            original_scalars = session.scalars

            async def scalars(statement, *args, **kwargs):
                result = await original_scalars(statement, *args, **kwargs)
                if getattr(statement, "_for_update_arg", None) is not None and any(
                    column.get("entity") is Video
                    for column in getattr(statement, "column_descriptions", ())
                ):
                    resolved.set()
                    await release.wait()
                return result

            session.scalars = scalars
            user = await session.get(User, user_id)
            if dependency == "product":
                await patch_product(
                    product_id,
                    ProductPatch(context={"feature": "graph editor"}),
                    revision=1,
                    user=user,
                    session=session,
                )
            else:
                await patch_brand(
                    brand_id,
                    BrandPatch(context={"tone": "graph editor"}),
                    revision=1,
                    user=user,
                    session=session,
                )

    def record_sql(_connection, _cursor, sql, _parameters, _context, _many):
        statements.append(sql)

    task = asyncio.create_task(edit())
    try:
        async with asyncio.timeout(15):
            await resolved.wait()
            async with factory() as session:
                video = await session.get(Video, video_id, with_for_update=True)
                if dependency == "product":
                    video.product_id = product_id
                else:
                    video.brand_id = brand_id
                video.revision += 1
                assert video in session.dirty and session.is_modified(video)
                connection = (await session.connection()).sync_connection
                event.listen(connection, "before_cursor_execute", record_sql)
                try:
                    with pytest.raises(AppError) as raised:
                        # No waiting on graph while the caller already holds Video.
                        async with asyncio.timeout(3):
                            await GenerationService(
                                Settings(_env_file=None, min_free_disk_bytes=0)
                            ).create(
                                session,
                                scene_id=scene_id,
                                request=GenerationRequest(seed=42, workflow_id=workflow_id),
                                user_id=user_id,
                                request_id=None,
                            )
                    assert raised.value.status_code == 412
                    assert not [
                        sql
                        for sql in statements
                        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                    ], statements
                finally:
                    event.remove(connection, "before_cursor_execute", record_sql)
                    await session.rollback()
            release.set()
            await task
        async with factory() as observer:
            video = await observer.get(Video, video_id)
            assert video.product_id is None and video.brand_id is None
            assert video.revision == 1
            model, identity = (
                (Product, product_id) if dependency == "product" else (Brand, brand_id)
            )
            row = await observer.get(model, identity)
            assert row.revision == 2
            assert row.context == (
                {"feature": "graph editor"} if dependency == "product" else {"tone": "graph editor"}
            )
            ids = list(
                (
                    await observer.scalars(
                        select(SceneGeneration.id).where(SceneGeneration.scene_id == scene_id)
                    )
                ).all()
            )
            assert ids == [old_generation_id]
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await _disable_identity_workflow(factory, workflow_id)
