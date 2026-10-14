"""Worker provenance and assembly state regressions; no application DB or external I/O."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, func, inspect, select, update

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, FinalVideo, Scene, SceneGeneration, Video
from apps.api.app.schemas.api import AssemblyRequest
from apps.api.app.services.assembly_service import AssemblyService
from tests.integration.test_assembly_manifest import seed_ready_video
from tests.integration.test_output_file_persistence import FileStore
from workers import common
from workers.assembler import Assembler
from workers.director_dispatcher import DirectorDispatcher
from workers.dispatcher import Dispatcher


def make_worker(worker_type, factory, store):
    settings = SimpleNamespace(lease_seconds=30)
    if worker_type is Assembler:
        return worker_type(factory, store, None, settings)
    return worker_type(factory, None, store, None, settings)


async def output_args(factory, tmp_path, *, final_output=False):
    user_id, video_id, scene_id = await seed_ready_video(factory)
    async with factory() as session, session.begin():
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        owner_id = scene.selected_generation_id
        if final_output:
            final = await AssemblyService().create(
                session, video_id=video_id, request=AssemblyRequest(),
                expected_revision=video.revision, user_id=user_id, request_id=None,
            )
            owner_id = final.id
        else:
            # The shared assembly seed already owns a canonical scene-one output.
            # Persist this payload for a fresh generation, then corrupt that
            # output below; replacing the seed's bytes would fail during setup.
            selected = await session.get(SceneGeneration, owner_id)
            generation = SceneGeneration(
                video_id=video_id,
                scene_id=scene_id,
                mode=selected.mode,
                workflow_id=selected.workflow_id,
                generation_no=selected.generation_no + 1,
                operation="ORIGINAL",
                status="RUNNING",
                phase="GENERATING",
                input_snapshot=selected.input_snapshot,
                created_by=user_id,
            )
            session.add(generation)
            await session.flush()
            owner_id = generation.id
        args = dict(
            owner_id=owner_id, role="FINAL_VIDEO" if final_output else "GENERATED_VIDEO",
            project_id=video.project_id, video_id=video_id, created_by=user_id,
            path=tmp_path / "output.mp4",
            metadata={"kind": "VIDEO", "has_video": True, "width": 16, "height": 9},
        )
    args["path"].write_bytes(b"immutable-output-payload")
    return args


def asset_state(asset):
    return {
        column.key: getattr(asset, column.key)
        for column in Asset.__table__.columns
    }


@pytest.mark.parametrize("worker_type", [Dispatcher, DirectorDispatcher, Assembler])
async def test_legacy_output_conflict_has_specific_terminal_classification(
    session_factory, tmp_path, monkeypatch, worker_type,
):
    args = await output_args(session_factory, tmp_path, final_output=worker_type is Assembler)
    store = FileStore()
    asset_id = await common.save_output_file(session_factory, store, **args)
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id)
        asset.resource_owner_id = None
        asset.ownership_provenance = None
    async with session_factory() as session:
        before = asset_state(await session.get(Asset, asset_id))
        count = await session.scalar(select(func.count()).select_from(Asset))
    old_objects = dict(store.objects)
    worker = make_worker(worker_type, session_factory, store)
    job_id = args["owner_id"]
    finish, retry = AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, "claim", AsyncMock(return_value=job_id))
    monkeypatch.setattr(worker, "_finish", finish)
    monkeypatch.setattr(worker, "_retry_or_finish", retry)

    async def persist_output(_job_id):
        await common.save_output_file(session_factory, store, **args)

    monkeypatch.setattr(worker, "process", persist_output)
    assert await worker.run_once() is True
    retry.assert_not_awaited()
    assert store.uploads == 1
    assert store.objects == old_objects
    assert store.deleted == []
    async with session_factory() as session:
        assert asset_state(await session.get(Asset, asset_id)) == before
        assert await session.scalar(select(func.count()).select_from(Asset)) == count
    finish.assert_awaited_once()
    call = finish.await_args
    assert call.args[:2] == (job_id, "FAILED")
    code = call.kwargs["code"] if worker_type is Assembler else call.args[2]
    message = call.kwargs["message"] if worker_type is Assembler else call.args[3]
    assert message == "IMMUTABLE_OUTPUT_CONFLICT"
    assert code == "IMMUTABLE_OUTPUT_CONFLICT"


@pytest.mark.parametrize("worker_type", [Dispatcher, DirectorDispatcher, Assembler])
@pytest.mark.parametrize(
    "error", [ValueError("bad input"), ValueError("IMMUTABLE_OUTPUT_CONFLICT extra"),
              KeyError("IMMUTABLE_OUTPUT_CONFLICT")],
)
async def test_other_value_errors_keep_input_invalid_classification(
    session_factory, monkeypatch, worker_type, error,
):
    worker = make_worker(worker_type, session_factory, FileStore())
    finish = AsyncMock()
    monkeypatch.setattr(worker, "claim", AsyncMock(return_value="job"))
    monkeypatch.setattr(worker, "process", AsyncMock(side_effect=error))
    monkeypatch.setattr(worker, "_finish", finish)
    assert await worker.run_once() is True
    call = finish.await_args
    code = call.kwargs["code"] if worker_type is Assembler else call.args[2]
    expected = "ASSEMBLY_INPUT_INVALID" if worker_type is Assembler else "GENERATION_INPUT_INVALID"
    assert code == expected


@pytest.mark.parametrize("field", ["ownership_provenance", "resource_owner_id", "checksum"])
async def test_immutable_retry_never_overwrites_or_backfills(
    session_factory, tmp_path, field,
):
    args = await output_args(session_factory, tmp_path)
    store = FileStore()
    asset_id = await common.save_output_file(session_factory, store, **args)
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id)
        setattr(asset, field, "0" * 64 if field == "checksum" else None)
    async with session_factory() as session:
        before = asset_state(await session.get(Asset, asset_id))
    objects = dict(store.objects)
    with pytest.raises(ValueError, match="^IMMUTABLE_OUTPUT_CONFLICT$"):
        await common.save_output_file(session_factory, store, **args)
    assert store.uploads == 1
    assert store.objects == objects
    assert store.deleted == []
    async with session_factory() as session:
        assert asset_state(await session.get(Asset, asset_id)) == before


@pytest.mark.parametrize(
    ("target", "expected_code"),
    [("scene", "SCENE_SELECTION_REQUIRED"), ("generation", "SCENE_GENERATION_NOT_READY"),
     ("asset", "SCENE_ASSET_NOT_READY")],
)
async def test_assembly_revalidates_preloaded_state_at_write_boundary(
    session_factory, target, expected_code,
):
    user_id, video_id, scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        generation = await session.get(SceneGeneration, scene.selected_generation_id)
        asset = await session.get(Asset, generation.output_asset_id)
        await AssemblyService().authorize_inputs(
            session, video_id=video_id, request=AssemblyRequest(), user_id=user_id,
        )
        model, resource_id, changes = {
            "scene": (Scene, scene.id, {"selected_generation_id": None}),
            "generation": (SceneGeneration, generation.id, {"status": "FAILED"}),
            "asset": (Asset, asset.id, {"status": "DELETED"}),
        }[target]
        # Change authoritative SQL state while retaining the preflight identity map.
        # This is an identity-map reproduction, not PostgreSQL concurrency evidence.
        await session.execute(
            update(model).where(model.id == resource_id).values(**changes)
            .execution_options(synchronize_session=False)
        )
        with pytest.raises(AppError) as rejected:
            await AssemblyService().create(
                session, video_id=video_id, request=AssemblyRequest(),
                expected_revision=video.revision, user_id=user_id, request_id=None,
            )
        assert rejected.value.code == expected_code
        assert await session.scalar(select(func.count()).select_from(FinalVideo)) == 0
        assert video.status != "ASSEMBLING"


@pytest.mark.parametrize("target", ["scene", "generation"])
@pytest.mark.parametrize("conflicting_revision", [False, True])
async def test_assembly_locked_refresh_preserves_pending_edits_without_autoflush(
    session_factory, target, conflicting_revision,
):
    user_id, video_id, scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        scene = await session.get(Scene, scene_id)
        generation = await session.get(SceneGeneration, scene.selected_generation_id)
        row, field, value, code = {
            "scene": (scene, "selected_generation_id", None, "SCENE_SELECTION_REQUIRED"),
            "generation": (generation, "status", "FAILED", "SCENE_GENERATION_NOT_READY"),
        }[target]
        setattr(row, field, value)
        history = inspect(row).attrs[field].history
        if conflicting_revision:
            await session.execute(
                update(type(row)).where(type(row).id == row.id)
                .values(revision=row.revision + 1)
                .execution_options(synchronize_session=False)
            )
        session.autoflush = True
        flushes = []
        event.listen(session.sync_session, "before_flush", lambda *args: flushes.append(True))
        with pytest.raises(AppError) as rejected:
            await AssemblyService().create(
                session, video_id=video_id, request=AssemblyRequest(),
                expected_revision=video.revision, user_id=user_id, request_id=None,
            )
        assert rejected.value.code == ("REVISION_CONFLICT" if conflicting_revision else code)
        assert getattr(row, field) == value
        assert inspect(row).attrs[field].history == history
        assert row in session.dirty
        assert flushes == []
        with session.no_autoflush:
            assert await session.scalar(select(func.count()).select_from(FinalVideo)) == 0
        await session.rollback()
