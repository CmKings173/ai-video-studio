"""Correction findings 2/3 reject invalid execution identities before persistence."""

import copy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from apps.api.app.api.generations import create_regeneration, create_variation, generate_all
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    DirectorRun,
    DirectorRunMember,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.schemas.api import (
    GenerateAll,
    GenerationRequest,
    RegenerateRequest,
    VariationRequest,
)
from apps.api.app.services.generation_freshness import is_generation_fresh
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_director_dynamic_aggregate import qualified_batch_state, submit_batch
from tests.integration.test_generate_all import submit
from tests.integration.test_generation_freshness import generated_selection
from tests.integration.test_generation_preparation import director_workflow_data


def settings(tmp_path):
    return Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)


async def standalone_workflow(session, user):
    graph, slots, profile, approved = director_workflow_data(quality="BASE")
    workflow = WorkflowRecord(
        code="STANDALONE_OVERRIDE", mode="t2v", version="1", quality_profile="BASE", workflow=graph,
        slots=slots, required_slots=list(slots), profile=profile,
        workflow_hash=approved.workflow_hash, slot_map_hash=approved.slot_map_hash,
        enabled=True, created_by=user,
    )
    session.add(workflow)
    await session.flush()
    return workflow.id


async def counts(session):
    # Flush before counting, inside the caller's transaction, so rollback cannot hide inserts.
    await session.flush()
    return tuple(
        [
            await session.scalar(select(func.count()).select_from(model))
            for model in (SceneGeneration, DirectorRun, DirectorRunMember)
        ]
    )


@pytest.mark.parametrize(
    "boundaries, selected, required",
    [
        (["CUT", "CONTINUOUS", "CONTINUOUS"], [0, 1], [0, 1, 2]),
        (["CUT", "CONTINUOUS", "CONTINUOUS"], [1, 2], [0, 1, 2]),
        (["CUT", "CONTINUOUS", "CONTINUOUS"], [1], [0, 1, 2]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [0], [0, 1]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [2], [2, 3]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [0, 1, 2], [2, 3]),
    ],
)
async def test_partial_chain_rejected_before_persistence(
    session_factory, tmp_path, monkeypatch, boundaries, selected, required
):
    user, video, ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, boundaries
    )
    provided = [ids[index] for index in selected]
    async with session_factory() as session, session.begin():
        assert await counts(session) == (0, 0, 0)
        with pytest.raises(AppError) as error:
            await generate_all(
                video_id=video,
                payload=GenerateAll(
                    scene_ids=provided,
                    settings=GenerationRequest(quality_profile="BASE", seed=42),
                ),
                request=SimpleNamespace(state=SimpleNamespace(request_id="chain-correction")),
                key="partial-chain",
                user=await session.get(User, user),
                session=session,
                settings=settings(tmp_path),
            )
        assert error.value.status_code == 422
        assert error.value.code == "DIRECTOR_CONTINUITY_CHAIN_REQUIRED"
        assert error.value.details == {
            "required_scene_ids": [ids[index] for index in required],
            "provided_scene_ids": provided,
        }
        assert await counts(session) == (0, 0, 0)


@pytest.mark.parametrize(
    "boundaries, selected",
    [
        (["CUT", "CONTINUOUS", "CONTINUOUS"], [2, 0, 1]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [0, 1]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [2, 3]),
        (["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"], [0, 1, 2, 3]),
    ],
)
async def test_complete_explicit_chains_with_exact_revisions_are_allowed(
    session_factory, tmp_path, monkeypatch, boundaries, selected
):
    user, video, ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, boundaries
    )
    provided = [ids[index] for index in selected]
    async with session_factory() as session:
        revision = (await session.get(Video, video)).revision
        revisions = {key: (await session.get(Scene, key)).revision for key in provided}
    result = await submit(
        session_factory, tmp_path, user, video,
        GenerateAll(
            scene_ids=provided,
            expected_video_revision=revision,
            expected_scene_revisions=revisions,
            settings=GenerationRequest(quality_profile="BASE", seed=42),
        ),
    )
    assert [str(row.scene_id) for row in result.generations] == [
        key for key in ids if key in provided
    ]


@pytest.mark.parametrize("revision_indices", [[0], [0, 1, 2]])
async def test_explicit_chain_revision_coverage_must_be_exact(
    session_factory, tmp_path, monkeypatch, revision_indices
):
    user, video, ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CUT"]
    )
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as error:
            await generate_all(
                video_id=video,
                payload=GenerateAll(
                    scene_ids=ids[:2],
                    expected_video_revision=(await session.get(Video, video)).revision,
                    expected_scene_revisions={
                        ids[index]: (await session.get(Scene, ids[index])).revision
                        for index in revision_indices
                    },
                ),
                request=SimpleNamespace(state=SimpleNamespace(request_id="revision-correction")),
                key="wrong-coverage",
                user=await session.get(User, user),
                session=session,
                settings=settings(tmp_path),
            )
        assert error.value.code == "REVISION_CONFLICT"
        assert await counts(session) == (0, 0, 0)


@pytest.mark.parametrize("operation, override", [
    ("VARIATION", False), ("VARIATION", True), ("REGENERATE", False),
])
@pytest.mark.parametrize("member", [0, 1])
@pytest.mark.parametrize("snapshot_association", [True, False])
async def test_aggregate_parent_derivative_rejected_before_new_rows(
    session_factory, tmp_path, monkeypatch, operation, member, override, snapshot_association
):
    user, video, scenes = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS"]
    )
    batch = await submit_batch(session_factory, tmp_path, user, video)
    parents = [str(row.id) for row in batch.generations]
    async with session_factory() as session, session.begin():
        parent = await session.get(SceneGeneration, parents[member])
        parent.status = "COMPLETED"
        if not snapshot_association:
            snapshot = copy.deepcopy(parent.input_snapshot)
            snapshot["generation_freshness"].pop("execution_group")
            parent.input_snapshot = snapshot
        frozen = copy.deepcopy(parent.input_snapshot)
        baseline = await counts(session)
        values = {"parent_generation_id": parent.id}
        if override:
            values.update(workflow_id=await standalone_workflow(session, user), mode="t2v")
        endpoint = create_variation if operation == "VARIATION" else create_regeneration
        payload_type = VariationRequest if operation == "VARIATION" else RegenerateRequest
        with pytest.raises(AppError) as error:
            await endpoint(
                scene_id=scenes[member], payload=payload_type(**values),
                request=SimpleNamespace(state=SimpleNamespace(request_id="derivative-correction")),
                key="aggregate-derivative", user=await session.get(User, user),
                session=session, settings=settings(tmp_path),
            )
        assert error.value.status_code == 422
        assert error.value.code == "DIRECTOR_DERIVATIVE_REQUIRES_CHAIN"
        assert await counts(session) == baseline
        assert parent.input_snapshot == frozen


@pytest.mark.parametrize("include_chain", [False, True])
async def test_independent_cut_outside_chain_can_be_selected(
    session_factory, tmp_path, monkeypatch, include_chain
):
    user, video, ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CONTINUOUS"]
    )
    async with session_factory() as session, session.begin():
        await standalone_workflow(session, user)
        independent = Scene(
            video_id=video, scene_order=3, prompt="Independent CUT", duration_seconds=5,
            spec={"continuity": "CUT"},
        )
        session.add(independent)
        await session.flush()
        independent_id = independent.id
    selected = [*ids, independent_id] if include_chain else [independent_id]
    result = await submit(
        session_factory, tmp_path, user, video,
        GenerateAll(
            scene_ids=selected, settings=GenerationRequest(quality_profile="BASE", seed=42)
        ),
    )
    assert [str(row.scene_id) for row in result.generations] == selected
    assert result.execution_groups[-1].execution_scope == "single_scene"


@pytest.mark.parametrize("operation", ["VARIATION", "REGENERATE"])
@pytest.mark.parametrize("edited", [False, True])
async def test_standalone_derivative_preserves_historical_identity(
    session_factory, tmp_path, operation, edited
):
    user, video_id, scene_id, parent_id = await generated_selection(session_factory, tmp_path)
    async with session_factory() as session, session.begin():
        parent = await session.get(SceneGeneration, parent_id)
        frozen = copy.deepcopy(parent.input_snapshot["generation_freshness"])
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, video_id)
        if edited:
            scene.prompt = "Edited after historical generation"
            scene.revision += 1
        derivative = await GenerationService(settings(tmp_path)).create(
            session, scene_id=scene_id,
            request=GenerationRequest(operation=operation, parent_generation_id=parent_id),
            user_id=user, request_id=None,
        )
        assert derivative.input_snapshot["generation_freshness"] == frozen
        assert await is_generation_fresh(session, scene, video, derivative) is (not edited)
