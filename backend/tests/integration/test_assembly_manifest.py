import copy
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    FinalVideoScene,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.integrations.ffmpeg import FFmpegError
from apps.api.app.schemas.api import AssemblyRequest
from apps.api.app.services.assembly_service import AssemblyService, manifest_hash
from workers.assembler import Assembler


async def seed_ready_video(session_factory):
    checksum = hashlib.sha256(b"scene-one").hexdigest()
    async with session_factory() as session, session.begin():
        user = User(email="assembly@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        workflow = WorkflowRecord(
            code="ASSEMBLY_TEST",
            mode="t2v",
            version="1",
            workflow={"1": {"inputs": {"prompt": ""}}},
            slots={"PROMPT": ["1", "prompt"]},
            required_slots=["PROMPT"],
            profile={},
            workflow_hash="a" * 64,
            slot_map_hash="b" * 64,
            enabled=True,
            created_by=user.id,
        )
        video = Video(
            project_id=project.id,
            title="Long launch",
            brief="Launch",
            kind="LONG_VIDEO",
            target_duration=30,
            created_by=user.id,
        )
        session.add_all([workflow, video])
        await session.flush()
        scene = Scene(
            video_id=video.id,
            scene_order=0,
            prompt="Scene one",
            duration_seconds=5,
        )
        asset = Asset(
            project_id=project.id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="scene.mp4",
            content_type="video/mp4",
            object_key=f"generated/{checksum}.mp4",
            status="READY",
            size_bytes=9,
            checksum=checksum,
            width=480,
            height=864,
            duration_seconds=5,
            created_by=user.id,
        )
        session.add_all([scene, asset])
        await session.flush()
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode="t2v",
            workflow_id=workflow.id,
            generation_no=1,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={"scene_revision": 1},
            output_asset_id=asset.id,
            created_by=user.id,
        )
        session.add(generation)
        await session.flush()
        scene.selected_generation_id = generation.id
        return user.id, video.id, scene.id


@pytest.mark.asyncio
async def test_assembly_freezes_selected_assets_and_checksums(session_factory):
    user_id, video_id, scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(transition="CUT", width=1080, height=1920),
            expected_revision=1,
            user_id=user_id,
            request_id="assemble-request",
        )
        frozen = copy.deepcopy(final.manifest)
        final_id = final.id

    assert manifest_hash(frozen) == (await _get_final(session_factory, final_id)).manifest_hash
    assert frozen["scenes"][0]["scene_id"] == scene_id
    assert len(frozen["scenes"][0]["asset_checksum"]) == 64

    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        scene.prompt = "Edited after assembly enqueue"
        scene.revision += 1
        video.revision += 1

    stored = await _get_final(session_factory, final_id)
    assert stored.manifest == frozen
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(FinalVideoScene).where(FinalVideoScene.final_video_id == final_id)
                )
            ).all()
        )
        assert rows[0].asset_checksum == frozen["scenes"][0]["asset_checksum"]


async def _get_final(session_factory, final_id):
    async with session_factory() as session:
        return await session.get(FinalVideo, final_id)


@pytest.mark.asyncio
async def test_only_one_assembly_can_be_active_per_video(session_factory):
    user_id, video_id, _scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=1,
            user_id=user_id,
            request_id=None,
        )
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError, match="active assembly"):
            await AssemblyService().create(
                session,
                video_id=video_id,
                request=AssemblyRequest(),
                expected_revision=1,
                user_id=user_id,
                request_id=None,
            )


class MemoryStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def put_bytes(self, key: str, data: bytes, _content_type: str) -> dict:
        existing = self.objects.get(key)
        if existing is not None and existing != data:
            raise RuntimeError("immutable conflict")
        self.objects[key] = data
        return {"key": key, "checksum": hashlib.sha256(data).hexdigest(), "size": len(data)}


class CapturingFFmpeg:
    def __init__(self, *, fail_once: bool = False):
        self.fail_once = fail_once
        self.calls = 0
        self.input_payloads: list[bytes] = []

    async def assemble(self, inputs, output, _config):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise FFmpegError("temporary encoder failure")
        self.input_payloads = [path.read_bytes() for path in inputs]
        output.write_bytes(b"final-video")
        return {"width": 1080, "height": 1920, "duration_seconds": 5.0, "has_video": True}


def worker_settings(tmp_path):
    return SimpleNamespace(
        lease_seconds=10,
        worker_poll_seconds=0.01,
        min_free_disk_bytes=0,
        max_upload_bytes=1024 * 1024,
        workspace_root=tmp_path,
        max_assembly_attempts=2,
    )


@pytest.mark.asyncio
async def test_assembler_uses_frozen_manifest_when_live_scene_changes(
    session_factory, tmp_path
):
    user_id, video_id, scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=1,
            user_id=user_id,
            request_id="assemble-frozen",
        )
        final_id = final.id
        input_key = final.manifest["scenes"][0]["asset_object_key"]
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id, with_for_update=True)
        video = await session.get(Video, video_id, with_for_update=True)
        scene.prompt = "new live prompt"
        scene.revision += 1
        video.revision += 1

    store = MemoryStore()
    store.objects[input_key] = b"scene-one"
    ffmpeg = CapturingFFmpeg()
    assembler = Assembler(session_factory, store, ffmpeg, worker_settings(tmp_path))

    assert await assembler.run_once() is True

    async with session_factory() as session:
        final = await session.get(FinalVideo, final_id)
        video = await session.get(Video, video_id)
        output = await session.get(Asset, final.output_asset_id)
        assert final.status == "READY"
        assert final.manifest["scenes"][0]["asset_object_key"] == input_key
        assert output.status == "READY"
        assert video.status == "DIRTY"
        assert video.current_final_video_id is None
    assert ffmpeg.input_payloads == [b"scene-one"]


@pytest.mark.asyncio
async def test_assembler_retries_one_transient_ffmpeg_failure(session_factory, tmp_path):
    user_id, video_id, _scene_id = await seed_ready_video(session_factory)
    async with session_factory() as session, session.begin():
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=1,
            user_id=user_id,
            request_id="assemble-retry",
        )
        final_id = final.id
        input_key = final.manifest["scenes"][0]["asset_object_key"]
    store = MemoryStore()
    store.objects[input_key] = b"scene-one"
    ffmpeg = CapturingFFmpeg(fail_once=True)
    assembler = Assembler(session_factory, store, ffmpeg, worker_settings(tmp_path))

    assert await assembler.run_once() is True
    async with session_factory() as session:
        first = await session.get(FinalVideo, final_id)
        assert first.status == "QUEUED"
        assert first.attempt_count == 1

    assert await assembler.run_once() is True
    async with session_factory() as session:
        completed = await session.get(FinalVideo, final_id)
        assert completed.status == "READY"
        assert completed.attempt_count == 2
    assert ffmpeg.calls == 2
