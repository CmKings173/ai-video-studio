import copy
import json
from pathlib import Path

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Project, Scene, User, Video, WorkflowRecord
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.executable_weights import collect_executable_weights
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.idempotency import claim, complete
from apps.api.app.services.workflow_contracts import profile_hash
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.contract_fixtures import synthetic_profile


def workflow_data():
    graph = {
        "1": {
            "class_type": "H3",
            "inputs": {
                "prompt": "",
                "negative": "",
                "seed": 0,
                "width": 480,
                "height": 864,
                "length": 124,
                "duration": 5,
                "steps": 8,
            },
        },
        "2": {"class_type": "SaveVideo", "inputs": {"prefix": "old"}},
    }
    slots = {
        "PROMPT": ["1", "prompt"],
        "NEGATIVE_PROMPT": ["1", "negative"],
        "SEED": ["1", "seed"],
        "WIDTH": ["1", "width"],
        "HEIGHT": ["1", "height"],
        "LENGTH": ["1", "length"],
        "DURATION": ["1", "duration"],
        "STEPS": ["1", "steps"],
        "OUTPUT_PREFIX": ["2", "prefix"],
    }
    approved = ApprovedWorkflow(
        mode="t2v",
        version="1",
        workflow=graph,
        slots={name: tuple(binding) for name, binding in slots.items()},
        required_slots=frozenset(slots),
    )
    return graph, slots, approved


def director_workflow_data(
    *, mode="t2v", quality="STANDARD", steps=12, execution_scope="single_scene"
):
    """Real pinned single-scene graph with synthetic evidence for isolated tests only."""
    directory = Path(__file__).resolve().parents[2] / "workflows/h3"
    entry = next(
        item
        for item in json.loads((directory / "registry.json").read_text())["workflows"]
        if item["code"]
        == f"H3_DIRECTOR_{mode.upper()}_BASE"
        + ("_AGGREGATE" if execution_scope == "aggregate" else "")
    )
    graph = json.loads((directory / entry["file"]).read_text())
    slots = copy.deepcopy(entry["slots"])
    approved = ApprovedWorkflow(
        mode,
        "1",
        graph,
        {k: tuple(v) for k, v in slots.items()},
        execution_scope=execution_scope,
    )
    legacy_graph, legacy_slots, _ = workflow_data()
    baseline, _ = synthetic_profile(
        legacy_graph, legacy_slots, mode=mode, quality=quality, steps=steps
    )
    profile = copy.deepcopy(entry["profile"])
    profile.update(
        quality_profile=quality,
        steps=steps,
        resolution=baseline["resolution"],
        dependency_versions={n["class_type"]: "synthetic-test-only" for n in graph.values()},
        weight_hashes={w: "a" * 64 for w in collect_executable_weights(graph)},
    )
    evidence = baseline["execution_evidence"]
    evidence.update(
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
        profile_hash=profile_hash(profile),
        dependency_versions=profile["dependency_versions"],
        weight_hashes=profile["weight_hashes"],
    )
    profile.update(poc_verified=True, execution_evidence=evidence)
    return graph, slots, profile, approved


async def seed(session_factory):
    graph, slots, profile, approved = director_workflow_data()
    async with session_factory() as session, session.begin():
        user = User(email="editor@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Launch",
            brief="A premium launch",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="9:16",
            created_by=user.id,
        )
        workflow = WorkflowRecord(
            code="H3_TEST",
            mode="t2v",
            version="1",
            workflow=graph,
            slots=slots,
            required_slots=list(slots),
            profile=profile,
            workflow_hash=approved.workflow_hash,
            slot_map_hash=approved.slot_map_hash,
            enabled=True,
            created_by=user.id,
        )
        session.add_all([video, workflow])
        await session.flush()
        scene = Scene(
            video_id=video.id,
            scene_order=0,
            prompt="Bottle rotates in warm light",
            negative_prompt="warped label",
            duration_seconds=5,
            spec={"subject": "Atlas bottle", "camera": "slow dolly"},
        )
        session.add(scene)
        await session.flush()
        return user.id, scene.id


@pytest.mark.asyncio
async def test_generation_creation_freezes_every_execution_input(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        workspace_root=tmp_path,
        min_free_disk_bytes=0,
    )
    async with session_factory() as session, session.begin():
        generation = await GenerationService(settings).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42, steps=12),
            user_id=user_id,
            request_id="request-1",
        )
        await session.flush()
        snapshot = copy.deepcopy(generation.input_snapshot)
        generation_id = generation.id

    assert snapshot["seed"] == 42
    assert snapshot["frames"] == 124
    assert snapshot["width"] == 480
    assert snapshot["height"] == 864
    assert snapshot["scene_revision"] == 1
    assert snapshot["director_execution"]["prompt"] == snapshot["prompt"]
    assert snapshot["generation_intent"]["prompt"] == snapshot["prompt"]

    async with session_factory() as session:
        stored = await session.get(type(generation), generation_id)
        assert stored.input_snapshot == snapshot
        assert stored.mode == "t2v"
        assert stored.status == "CREATED"


@pytest.mark.asyncio
async def test_idempotency_replays_same_payload_and_rejects_reuse(session_factory):
    user_id, _scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        record, replay = await claim(
            session,
            user_id=user_id,
            operation="generate:scene",
            key="click-1",
            payload={"seed": 1},
        )
        assert replay is None
        complete(record, {"id": "generation-1"}, 202)

    async with session_factory() as session, session.begin():
        _record, replay = await claim(
            session,
            user_id=user_id,
            operation="generate:scene",
            key="click-1",
            payload={"seed": 1},
        )
        assert replay == {"id": "generation-1"}

    async with session_factory() as session, session.begin():
        with pytest.raises(AppError, match="different request"):
            await claim(
                session,
                user_id=user_id,
                operation="generate:scene",
                key="click-1",
                payload={"seed": 2},
            )
