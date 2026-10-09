from __future__ import annotations

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.core.config import Settings
from apps.api.app.db.models import (
    GenerationAttempt,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.integrations.comfy_adapter import ComfyError, SubmissionUncertain
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import director_workflow_data
from workers.dispatcher import Dispatcher


class MemoryStore:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_file_immutable(self, key, path, content_type, checksum, max_bytes):
        with path.open("rb") as source:
            data = b"".join(iter(lambda: source.read(1024 * 1024), b""))
        assert len(data) <= max_bytes
        assert hashlib.sha256(data).hexdigest() == checksum
        return await self.put_bytes(key, data, content_type)

    async def checksum_object(self, key, max_bytes):
        data = self.objects[key]
        assert len(data) <= max_bytes
        return {"size": len(data), "checksum": hashlib.sha256(data).hexdigest()}

    async def put_bytes(self, key: str, data: bytes, _content_type: str) -> dict:
        existing = self.objects.get(key)
        if existing is not None and existing != data:
            raise RuntimeError("immutable conflict")
        self.objects[key] = data
        return {"key": key, "checksum": hashlib.sha256(data).hexdigest(), "size": len(data)}

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


class ProbeOnlyFFmpeg:
    async def probe(self, _path):
        return {
            "width": 480,
            "height": 864,
            "duration_seconds": 124 / 24,
            "frames": 124,
            "fps": 24.0,
            "has_video": True,
            "has_audio": True,
        }


class UncertainThenRecoverAdapter:
    def __init__(self):
        self.submit_count = 0
        self.prompt_by_client: dict[str, str] = {}

    async def object_info(self) -> dict:
        # Synthetic installed node contract; the real builder still validates its inputs.
        graph, _, _, _ = director_workflow_data()
        result = {
            node["class_type"]: {
                "input": {"required": {name: ["TEST_ONLY"] for name in node["inputs"]}}
            }
            for node in graph.values()
        }
        result["MiniMaxH3Director"]["input"]["required"]["task_type"] = [
            ["t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"]
        ]
        return result

    async def submit(self, _workflow: dict, client_id: str) -> str:
        self.submit_count += 1
        prompt_id = f"prompt-{self.submit_count}"
        self.prompt_by_client[client_id] = prompt_id
        raise SubmissionUncertain("response was lost", "COMFY_SUBMISSION_UNCERTAIN")

    async def find_by_client_id(self, client_id: str) -> str | None:
        return self.prompt_by_client.get(client_id)

    async def get_history(self, prompt_id: str) -> dict:
        return {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "7": {
                    "videos": [{"filename": f"{prompt_id}.mp4", "subfolder": "", "type": "output"}]
                }
            },
        }

    async def get_queue(self) -> dict:
        return {"queue_running": [], "queue_pending": []}

    async def stream_progress(self, _client_id: str, _prompt_id: str):
        if False:
            yield {}

    @staticmethod
    def outputs(history: dict, _output_node: str | None = None) -> list[dict]:
        return history["outputs"][_output_node or "7"]["videos"]

    async def download_to_path(self, output, path, max_bytes=None):
        data = await self.download(output)
        assert max_bytes is None or len(data) <= max_bytes
        path.write_bytes(data)
        return {"path": path, "size": len(data), "checksum": hashlib.sha256(data).hexdigest()}

    async def download(self, _output: dict) -> bytes:
        return b"generated-video"


class RejectingAdapter(UncertainThenRecoverAdapter):
    async def submit(self, _workflow: dict, _client_id: str) -> str:
        self.submit_count += 1
        raise ComfyError("invalid workflow", "COMFY_REJECTED")


class FailOnceAdapter(UncertainThenRecoverAdapter):
    async def submit(self, _workflow: dict, client_id: str) -> str:
        self.submit_count += 1
        prompt_id = f"prompt-{self.submit_count}"
        self.prompt_by_client[client_id] = prompt_id
        return prompt_id

    async def get_history(self, prompt_id: str) -> dict:
        if prompt_id == "prompt-1":
            return {"status": {"completed": True, "status_str": "error"}, "outputs": {}}
        return await super().get_history(prompt_id)


class OversizedOutputAdapter(FailOnceAdapter):
    async def get_history(self, _prompt_id: str) -> dict:
        return {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"7": {"videos": [{"filename": "oversized.mp4"}]}},
        }

    async def download_to_path(self, _output, _path, max_bytes=None):
        assert max_bytes is not None
        raise ComfyError("Generated output exceeds size limit", "COMFY_OUTPUT_TOO_LARGE")


def worker_settings(tmp_path):
    return SimpleNamespace(
        lease_seconds=10,
        worker_poll_seconds=0.01,
        comfy_timeout_seconds=2,
        max_generation_attempts=2,
        max_upload_bytes=1024 * 1024,
        min_free_disk_bytes=0,
        workspace_root=tmp_path,
    )


async def seed_generation(session_factory, *, status="CREATED", phase="PENDING"):
    graph, slots, profile, approved = director_workflow_data()
    async with session_factory() as session, session.begin():
        user = User(email=f"{status.lower()}@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code=f"WORKFLOW_{status}",
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
            title="Clip",
            brief="Brief",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="9:16",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Prompt", duration_seconds=5)
        session.add(scene)
        await session.flush()
        generation = await GenerationService(
            Settings(_env_file=None, min_free_disk_bytes=0)
        ).create(
            session,
            scene_id=scene.id,
            request=GenerationRequest(seed=42),
            user_id=user.id,
            request_id=None,
        )
        generation.status, generation.phase = status, phase
        await session.flush()
        return generation.id


async def seed_fairness_queue(session_factory):
    async with session_factory() as session, session.begin():
        user = User(email="fairness@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Fairness", description="", created_by=user.id)
        workflow = WorkflowRecord(
            code="FAIRNESS",
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
        session.add_all([project, workflow])
        await session.flush()
        videos = [
            Video(
                project_id=project.id,
                title=f"Video {index}",
                brief="Brief",
                kind="LONG_VIDEO",
                target_duration=30,
                created_by=user.id,
            )
            for index in range(2)
        ]
        session.add_all(videos)
        await session.flush()
        scenes = [
            Scene(video_id=videos[0].id, scene_order=0, prompt="A1", duration_seconds=5),
            Scene(video_id=videos[0].id, scene_order=1, prompt="A2", duration_seconds=5),
            Scene(video_id=videos[1].id, scene_order=0, prompt="B1", duration_seconds=5),
        ]
        session.add_all(scenes)
        await session.flush()
        generations = []
        base_created_at = utcnow()
        for index, scene in enumerate(scenes):
            generation = SceneGeneration(
                video_id=scene.video_id,
                scene_id=scene.id,
                mode="t2v",
                workflow_id=workflow.id,
                generation_no=1,
                operation="ORIGINAL",
                status="CREATED",
                phase="PENDING",
                input_snapshot={"workflow": workflow.workflow, "slots": {}, "assets": []},
                created_by=user.id,
                created_at=base_created_at + timedelta(seconds=index),
            )
            session.add(generation)
            generations.append(generation)
            await session.flush()
        return videos[0].id, videos[1].id, [item.id for item in generations]


@pytest.mark.asyncio
async def test_uncertain_submit_is_recovered_by_correlation_without_duplicate(
    session_factory, tmp_path
):
    generation_id = await seed_generation(session_factory)
    adapter = UncertainThenRecoverAdapter()
    store = MemoryStore()
    first = Dispatcher(
        session_factory, adapter, store, ProbeOnlyFFmpeg(), worker_settings(tmp_path)
    )

    assert await first.run_once() is True
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
        assert generation.status == "DISPATCHING"
        assert generation.error_code == "SUBMISSION_UNCERTAIN"
        generation.lease_expires_at = utcnow()

    recovered = Dispatcher(
        session_factory, adapter, store, ProbeOnlyFFmpeg(), worker_settings(tmp_path)
    )
    assert await recovered.run_once() is True

    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        attempts = list(
            (
                await session.scalars(
                    select(GenerationAttempt).where(
                        GenerationAttempt.generation_id == generation_id
                    )
                )
            ).all()
        )
        assert generation.status == "COMPLETED", (generation.error_code, generation.error_message)
        assert generation.output_asset_id is not None
        assert len(attempts) == 1
        assert attempts[0].status == "COMPLETED"
        assert attempts[0].comfy_prompt_id == generation.comfy_prompt_id
    assert adapter.submit_count == 1


@pytest.mark.asyncio
async def test_known_workflow_rejection_fails_instead_of_waiting_for_reconciliation(
    session_factory, tmp_path
):
    generation_id = await seed_generation(session_factory)
    dispatcher = Dispatcher(
        session_factory,
        RejectingAdapter(),
        MemoryStore(),
        ProbeOnlyFFmpeg(),
        worker_settings(tmp_path),
    )

    assert await dispatcher.run_once() is True

    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "FAILED"
        assert generation.error_code == "COMFY_REJECTED"
        attempt = await session.scalar(
            select(GenerationAttempt).where(GenerationAttempt.generation_id == generation_id)
        )
        assert attempt.status == "FAILED"


@pytest.mark.asyncio
async def test_late_progress_cannot_regress_collecting_or_cancel_requested(
    session_factory, tmp_path
):
    generation_id = await seed_generation(session_factory, status="COLLECTING", phase="COLLECTING")
    dispatcher = Dispatcher(
        session_factory,
        UncertainThenRecoverAdapter(),
        MemoryStore(),
        ProbeOnlyFFmpeg(),
        worker_settings(tmp_path),
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
        generation.claimed_by = dispatcher.owner
        generation.lease_expires_at = utcnow()
        original_revision = generation.revision

    await dispatcher._update(
        generation_id,
        status="RUNNING",
        phase="GENERATING",
        progress_current=3,
        progress_total=10,
    )
    await dispatcher._update(generation_id)

    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
        assert generation.status == "COLLECTING"
        assert generation.phase == "COLLECTING"
        assert generation.revision == original_revision
        generation.status = "CANCEL_REQUESTED"
        generation.phase = "CANCELLING"
        generation.revision += 1
        cancel_revision = generation.revision

    await dispatcher._update(
        generation_id,
        status="RUNNING",
        phase="GENERATING",
        progress_current=4,
        progress_total=10,
    )

    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCEL_REQUESTED"
        assert generation.phase == "CANCELLING"
        assert generation.revision == cancel_revision


@pytest.mark.asyncio
async def test_execution_retry_creates_a_new_attempt_without_new_generation(
    session_factory, tmp_path
):
    generation_id = await seed_generation(session_factory)
    adapter = FailOnceAdapter()
    dispatcher = Dispatcher(
        session_factory, adapter, MemoryStore(), ProbeOnlyFFmpeg(), worker_settings(tmp_path)
    )

    assert await dispatcher.run_once() is True
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CREATED"
        assert generation.attempt_count == 1

    assert await dispatcher.run_once() is True
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        attempts = list(
            (
                await session.scalars(
                    select(GenerationAttempt)
                    .where(GenerationAttempt.generation_id == generation_id)
                    .order_by(GenerationAttempt.attempt_no)
                )
            ).all()
        )
        assert generation.status == "COMPLETED", (generation.error_code, generation.error_message)
        assert generation.attempt_count == 2
        assert [attempt.status for attempt in attempts] == ["FAILED", "COMPLETED"]
    assert adapter.submit_count == 2


@pytest.mark.asyncio
async def test_cancel_request_wins_when_completion_arrives_concurrently(session_factory, tmp_path):
    generation_id = await seed_generation(session_factory)
    adapter = FailOnceAdapter()
    dispatcher = Dispatcher(
        session_factory, adapter, MemoryStore(), ProbeOnlyFFmpeg(), worker_settings(tmp_path)
    )
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
        generation.status = "CANCEL_REQUESTED"
        generation.phase = "CANCELLING"
        generation.comfy_prompt_id = "prompt-2"
        generation.attempt_count = 1
        generation.lease_expires_at = utcnow()
        session.add(
            GenerationAttempt(
                generation_id=generation.id,
                attempt_no=1,
                status="QUEUED",
                client_id="cancel-client",
                comfy_prompt_id="prompt-2",
            )
        )

    assert await dispatcher.run_once() is True

    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "CANCELLED"
        assert generation.output_asset_id is None
        attempt = await session.scalar(
            select(GenerationAttempt).where(GenerationAttempt.generation_id == generation_id)
        )
        assert attempt.status == "CANCELLED"


@pytest.mark.asyncio
async def test_dispatcher_preserves_oldest_order_between_videos_when_both_are_waiting(
    session_factory, tmp_path
):
    first_video_id, second_video_id, generation_ids = await seed_fairness_queue(session_factory)
    dispatcher = Dispatcher(
        session_factory,
        UncertainThenRecoverAdapter(),
        MemoryStore(),
        ProbeOnlyFFmpeg(),
        worker_settings(tmp_path),
    )

    first_claim = await dispatcher.claim()
    assert first_claim == generation_ids[0]
    await dispatcher._finish(first_claim, "FAILED", code="TEST", message="finished")
    second_claim = await dispatcher.claim()

    async with session_factory() as session:
        second = await session.get(SceneGeneration, second_claim)
        assert second.id == generation_ids[1]
        assert second.video_id == first_video_id
        assert second.video_id != second_video_id


@pytest.mark.asyncio
async def test_oversized_accepted_output_fails_terminally_and_releases_admission(
    session_factory, tmp_path
):
    generation_id = await seed_generation(session_factory)
    adapter = OversizedOutputAdapter()
    dispatcher = Dispatcher(
        session_factory,
        adapter,
        MemoryStore(),
        ProbeOnlyFFmpeg(),
        worker_settings(tmp_path),
    )

    assert await dispatcher.run_once() is True
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        attempt = await session.scalar(
            select(GenerationAttempt).where(GenerationAttempt.generation_id == generation_id)
        )
        assert generation.status == "FAILED"
        assert generation.error_code == "COMFY_OUTPUT_TOO_LARGE"
        assert generation.output_asset_id is None
        assert generation.claimed_by is None
        assert generation.lease_expires_at is None
        assert attempt.status == "FAILED"
        assert attempt.error_code == "COMFY_OUTPUT_TOO_LARGE"

    async with session_factory() as session, session.begin():
        failed = await session.get(SceneGeneration, generation_id)
        next_generation = SceneGeneration(
            video_id=failed.video_id,
            scene_id=failed.scene_id,
            mode=failed.mode,
            workflow_id=failed.workflow_id,
            generation_no=2,
            operation="ORIGINAL",
            status="CREATED",
            phase="PENDING",
            input_snapshot=failed.input_snapshot,
            created_by=failed.created_by,
        )
        session.add(next_generation)
        await session.flush()
        next_generation_id = next_generation.id

    assert await dispatcher.run_once() is True
    assert adapter.submit_count == 2
    async with session_factory() as session:
        first = await session.get(SceneGeneration, generation_id)
        first_attempts = list(
            (
                await session.scalars(
                    select(GenerationAttempt).where(
                        GenerationAttempt.generation_id == generation_id
                    )
                )
            ).all()
        )
        second = await session.get(SceneGeneration, next_generation_id)
        assert first.status == "FAILED"
        assert first.attempt_count == 1
        assert len(first_attempts) == 1
        assert first_attempts[0].status == "FAILED"
        assert second.status == "FAILED"
