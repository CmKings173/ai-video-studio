import copy
from pathlib import Path

import pytest
from sqlalchemy import select

from apps.api.app.api.admin import approve_workflow
from apps.api.app.api.generations import generation_capabilities
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene, SceneGeneration, User, WorkflowRecord
from apps.api.app.schemas.api import GenerationRequest, WorkflowApproval
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.workflow_contracts import require_frozen
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.app.services.workflow_router import director_task_for_mode
from apps.api.management.retire_legacy_h3 import retire_legacy_h3
from tests.integration.test_generation_preparation import seed


@pytest.mark.parametrize("mode", ["i2v_last", "i2v_first_last", "fl2v"])
def test_frame_aliases_map_to_director_fl2v(mode):
    assert director_task_for_mode(mode) == "fl2v"


def test_bundled_registry_is_director_only_and_unqualified():
    directory = Path(__file__).resolve().parents[2] / "workflows" / "h3"
    manifests = load_manifests(directory)
    assert len(manifests) == 12
    assert {item["execution_scope"] for item in manifests} == {"single_scene", "aggregate"}
    for item in manifests:
        assert item["code"].startswith("H3_DIRECTOR_")
        assert (
            item["profile"]["provider_source"]["commit"]
            == "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb"
        )
        assert not item["profile"].get("poc_verified")
        assert not item["profile"].get("runtime_qualified")
        assert not item["profile"].get("advertised")
    graph_files = list(directory.glob("director*.json"))
    assert len(graph_files) == 12
    assert not any("_aggregate_2" in path.name for path in graph_files)


@pytest.mark.asyncio
async def test_legacy_auto_explicit_capability_approval_and_frozen_gates(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    service = GenerationService(
        Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    )
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        # Keep this graph explicitly historical even when the shared seed becomes Director.
        record.workflow = {"1": {"class_type": "HistoricalH3", "inputs": {}}}
        for explicit, code in ((None, "WORKFLOW_NOT_CONFIGURED"), (record.id, "WORKFLOW_RETIRED")):
            with pytest.raises(AppError) as caught:
                await service._workflow(session, "t2v", explicit)
            assert caught.value.code == code
        user = await session.get(User, user_id)
        caps = await generation_capabilities(user=user, session=session)
        assert not caps.available and not caps.combinations and not caps.disabled
        with pytest.raises(AppError) as caught:
            await approve_workflow(
                record.id, WorkflowApproval(enabled=True), admin=user, session=session
            )
        assert caught.value.code == "WORKFLOW_RETIRED"
        with pytest.raises(AppError) as caught:
            require_frozen({}, record)
        assert caught.value.code == "WORKFLOW_RETIRED"


@pytest.mark.asyncio
async def test_retirement_retains_fk_graph_snapshots_and_is_idempotent(session_factory):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        record.code = "H3_T2V_STANDARD"
        record.workflow = {"1": {"class_type": "HistoricalH3", "inputs": {}}}
        original = copy.deepcopy(record.workflow)
        scene = await session.get(Scene, scene_id)
        history = SceneGeneration(
            video_id=scene.video_id,
            scene_id=scene_id,
            workflow_id=record.id,
            generation_no=1,
            mode="t2v",
            created_by=user_id,
            input_snapshot={
                "workflow": original,
                "workflow_version": record.version,
                "workflow_hash": record.workflow_hash,
            },
        )
        session.add(history)
        await session.flush()
        snapshot = copy.deepcopy(history.input_snapshot)
        dry = await retire_legacy_h3(session)
        assert dry[0]["fk_dependencies"]["scene_generations.workflow_id"] == 1
        assert record.enabled
        await retire_legacy_h3(session, apply=True)
        await session.flush()
        assert not record.enabled and record.workflow == original
        second = await retire_legacy_h3(session, apply=True)
        assert second[0]["enabled_before"] is False
        assert history.input_snapshot == snapshot and history.workflow_id == record.id
        assert await session.get(WorkflowRecord, record.id) is record


@pytest.mark.asyncio
async def test_legacy_parent_derivative_cannot_replan_even_with_director_override(
    session_factory, tmp_path
):
    user, scene_id = await seed(session_factory)
    service = GenerationService(
        Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    )
    async with session_factory() as session, session.begin():
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=42),
            user_id=user,
            request_id=None,
        )
        parent.status = "COMPLETED"
        frozen = copy.deepcopy(parent.input_snapshot)
        workflow = await session.get(WorkflowRecord, parent.workflow_id)
        workflow.workflow = {"1": {"class_type": "HistoricalH3", "inputs": {}}}
        for operation in ("VARIATION", "REGENERATE"):
            with pytest.raises(AppError) as caught:
                await service.create(
                    session,
                    scene_id=scene_id,
                    request=GenerationRequest(
                        operation=operation,
                        parent_generation_id=parent.id,
                        mode="t2v",
                        workflow_id=workflow.id,
                    ),
                    user_id=user,
                    request_id=None,
                )
            assert caught.value.code == "WORKFLOW_RETIRED"
        assert parent.input_snapshot == frozen


@pytest.mark.asyncio
async def test_no_legacy_fallback_when_director_is_unqualified(session_factory, tmp_path):
    user, scene_id = await seed(session_factory)
    service = GenerationService(
        Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    )
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        record.profile = {**record.profile, "poc_verified": False}
        with pytest.raises(AppError) as caught:
            await service.create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(),
                user_id=user,
                request_id=None,
            )
        assert caught.value.code == "WORKFLOW_NOT_QUALIFIED"
        assert not any(isinstance(row, SceneGeneration) for row in session.new)


@pytest.mark.asyncio
async def test_retirement_deletes_only_unreferenced_exact_codes_and_replay_is_empty(
    session_factory,
):
    from apps.api.management.retire_legacy_h3 import LEGACY_H3_CODES

    user, _ = await seed(session_factory)
    async with session_factory() as session, session.begin():
        director = await session.scalar(select(WorkflowRecord))
        director_id = director.id
        for code in [*sorted(LEGACY_H3_CODES), "UNRELATED_CUSTOM"]:
            session.add(
                WorkflowRecord(
                    code=code,
                    mode="t2v",
                    version="historical",
                    workflow={"1": {"class_type": "Legacy", "inputs": {}}},
                    slots={},
                    required_slots=[],
                    profile={},
                    workflow_hash="a" * 64,
                    slot_map_hash="b" * 64,
                    enabled=False,
                    created_by=user,
                )
            )
        await session.flush()
        dry = await retire_legacy_h3(session)
        assert {row["code"] for row in dry} == LEGACY_H3_CODES
        assert {row["action"] for row in dry} == {"delete_unreferenced"}
        assert len(list((await session.scalars(select(WorkflowRecord))).all())) == 7
        await retire_legacy_h3(session, apply=True)
        assert await retire_legacy_h3(session, apply=True) == []
        remaining = list((await session.scalars(select(WorkflowRecord))).all())
        assert {row.code for row in remaining} == {director.code, "UNRELATED_CUSTOM"}
        assert await session.get(WorkflowRecord, director_id) is director


@pytest.mark.asyncio
async def test_json_only_history_retains_original_row(session_factory):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        row = await session.scalar(select(WorkflowRecord))
        row.code = "H3_T2V_STANDARD"
        row.workflow = {"1": {"class_type": "HistoricalH3", "inputs": {}}}
        scene = await session.get(Scene, scene_id)
        scene.spec = {"historical_audit": {"workflow_code": row.code}}
        await session.flush()
        report = await retire_legacy_h3(session, apply=True)
        assert report[0]["json_dependencies"] == {"scenes.spec": 1}
        assert report[0]["action"] == "retain_historical_identity_disable"
        assert not row.enabled
