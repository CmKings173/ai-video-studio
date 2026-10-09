import json

import pytest
from sqlalchemy import select

from apps.api.app.api.admin import approve_workflow
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import User, WorkflowRecord
from apps.api.app.schemas.api import WorkflowApproval
from apps.api.app.services.workflow_loader import seed_workflows
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.integration.test_generation_preparation import director_workflow_data


def synthetic_evidence(entry):
    # Synthetic assertions are test fixtures, never runtime qualification artifacts.
    graph = ApprovedWorkflow(
        mode=entry["mode"],
        version=entry["version"],
        workflow=entry["workflow"],
        slots={name: tuple(binding) for name, binding in entry["slots"].items()},
    )
    return {
        "poc_verified": True,
        "execution_evidence": {
            "executed": True,
            "workflow_hash": graph.workflow_hash,
            "slot_map_hash": graph.slot_map_hash,
            "tested_at": "2026-10-05T00:00:00Z",
            "prompt_id": "synthetic-only",
            "model_hash": "c" * 64,
            "lora_hashes": {},
            "comfyui_commit": "d" * 40,
            "custom_node_versions": {},
            "output": {
                "width": 864,
                "height": 480,
                "fps": 24,
                "duration_seconds": 5,
                "has_video": True,
                "has_audio": True,
                "checksum": "f" * 64,
                "size_bytes": 1000,
            },
        },
    }


@pytest.mark.parametrize("defect", ["fps", "duration_seconds", "nodes_null", "nodes_list"])
@pytest.mark.asyncio
async def test_invalid_evidence_admin_and_seed_fail_closed(session_factory, tmp_path, defect):
    admin = await admin_user(session_factory)
    entry = manifest()
    entry["profile"] = synthetic_evidence(entry)
    if defect.startswith("nodes"):
        entry["profile"]["custom_node_versions"] = None if defect == "nodes_null" else []
    else:
        entry["profile"]["execution_evidence"]["output"][defect] = True
    graph = ApprovedWorkflow(
        mode="t2v",
        version="1",
        workflow=entry["workflow"],
        slots={key: tuple(value) for key, value in entry["slots"].items()},
    )
    async with session_factory() as session, session.begin():
        record = WorkflowRecord(
            code="ADMIN_TEST",
            mode="t2v",
            version="1",
            workflow=entry["workflow"],
            slots=entry["slots"],
            required_slots=entry["required_slots"],
            profile=entry["profile"],
            workflow_hash=graph.workflow_hash,
            slot_map_hash=graph.slot_map_hash,
            enabled=False,
            created_by=admin.id,
        )
        session.add(record)
        await session.flush()
        with pytest.raises(AppError) as error:
            await approve_workflow(
                record.id, WorkflowApproval(enabled=True), admin=admin, session=session
            )
        assert error.value.code == "WORKFLOW_NOT_QUALIFIED"
        assert error.value.status_code == 409
        assert not record.enabled
    (tmp_path / "test.api.json").write_text(json.dumps(entry.pop("workflow")), encoding="utf-8")
    entry["file"] = "test.api.json"
    (tmp_path / "registry.json").write_text(json.dumps({"workflows": [entry]}), encoding="utf-8")
    assert await seed_workflows(session_factory, tmp_path, admin) == 1
    async with session_factory() as session:
        seeded = await session.scalar(
            select(WorkflowRecord).where(WorkflowRecord.code == entry["code"])
        )
        assert not seeded.enabled


def manifest():
    graph, slots, _, _ = director_workflow_data()
    return {
        "code": "QUALIFICATION_TEST_ONLY",
        "mode": "t2v",
        "version": "1",
        "workflow": graph,
        "slots": slots,
        "required_slots": [],
        "profile": {"poc_verified": False},
        "auto_approve": True,
    }


async def admin_user(factory):
    async with factory() as session, session.begin():
        user = User(email="qualification@example.test", role="ADMIN", password_hash="test")
        session.add(user)
        await session.flush()
        return user


@pytest.mark.asyncio
async def test_admin_cannot_enable_unexecuted_workflow(session_factory):
    admin = await admin_user(session_factory)
    entry = manifest()
    graph = ApprovedWorkflow(
        mode="t2v",
        version="1",
        workflow=entry["workflow"],
        slots={name: tuple(binding) for name, binding in entry["slots"].items()},
        required_slots=frozenset(entry["required_slots"]),
    )
    async with session_factory() as session, session.begin():
        record = WorkflowRecord(
            code=entry["code"],
            mode="t2v",
            version="1",
            workflow=entry["workflow"],
            slots=entry["slots"],
            required_slots=entry["required_slots"],
            profile=entry["profile"],
            workflow_hash=graph.workflow_hash,
            slot_map_hash=graph.slot_map_hash,
            enabled=False,
            created_by=admin.id,
        )
        session.add(record)
        await session.flush()
        with pytest.raises(AppError, match="executed"):
            await approve_workflow(
                record.id, WorkflowApproval(enabled=True), admin=admin, session=session
            )
        assert not record.enabled


@pytest.mark.asyncio
async def test_seed_never_autoapproves_unexecuted_manifest(session_factory, tmp_path):
    admin = await admin_user(session_factory)
    entry = manifest()
    (tmp_path / "test.api.json").write_text(json.dumps(entry.pop("workflow")), encoding="utf-8")
    entry["file"] = "test.api.json"
    (tmp_path / "registry.json").write_text(json.dumps({"workflows": [entry]}), encoding="utf-8")
    assert await seed_workflows(session_factory, tmp_path, admin) == 1
    async with session_factory() as session:
        record = await session.scalar(select(WorkflowRecord))
        assert record.enabled is False
        assert record.approved_at is None
