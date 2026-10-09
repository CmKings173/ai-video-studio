"""Synthetic evidence exercises real Studio generation and aggregate service boundaries."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from apps.api.app.api.generations import generate_all, generation_capabilities
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.providers.minimax_h3_director.collector import artifact_manifest
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.workflow_builder import DirectorWorkflowBuilder
from apps.api.app.schemas.api import GenerateAll, GenerationRequest
from apps.api.app.services import director_run_service as runs
from apps.api.app.services.executable_weights import collect_executable_weights
from apps.api.app.services.generation_intent import stable_hash
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.workflow_contracts import profile_hash, require_contract
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.integration.test_generation_preparation import seed

DIRECTORY = Path(__file__).resolve().parents[2] / "workflows" / "h3"


async def prepared_members(
    factory, tmp_path, count, *, persist=True, boundaries=None, motion_enabled=False
):
    user_id, scene_id = await seed(factory)
    directory = DIRECTORY
    entry = next(
        item
        for item in json.loads((directory / "registry.json").read_text())["workflows"]
        if item["code"] == "H3_DIRECTOR_T2V_BASE_AGGREGATE"
    )
    graph = json.loads((directory / entry["file"]).read_text())
    approved = ApprovedWorkflow(
        "t2v",
        entry["version"],
        graph,
        {key: tuple(value) for key, value in entry["slots"].items()},
        execution_scope="aggregate",
    )
    async with factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        evidence = copy.deepcopy(workflow.profile["execution_evidence"])
        profile = copy.deepcopy(entry["profile"])
        profile["dependency_versions"] = {
            node["class_type"]: "synthetic-test-only" for node in graph.values()
        }
        profile["weight_hashes"] = {value: "a" * 64 for value in collect_executable_weights(graph)}
        evidence.update(
            workflow_hash=approved.workflow_hash,
            slot_map_hash=approved.slot_map_hash,
            dependency_versions=profile["dependency_versions"],
            weight_hashes=profile["weight_hashes"],
            profile_hash=profile_hash(profile),
        )
        evidence["output"].update(width=864, height=480)
        evidence["combinations"] = [
            {
                "mode": "t2v",
                "quality_profile": "BASE",
                "aspect_ratio": "16:9",
                "width": 864,
                "height": 480,
                "steps": 25,
                "output": copy.deepcopy(evidence["output"]),
            }
        ]
        profile.update(poc_verified=True, execution_evidence=evidence)
        workflow.code, workflow.version = entry["code"], entry["version"]
        workflow.workflow, workflow.slots = graph, entry["slots"]
        workflow.required_slots = []
        workflow.quality_profile, workflow.execution_scope = "BASE", "aggregate"
        workflow.profile = profile
        workflow.workflow_hash, workflow.slot_map_hash = (
            approved.workflow_hash,
            approved.slot_map_hash,
        )
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.aspect_ratio = "16:9"
        scene.negative_prompt = ""
        scene.spec = {"continuity": "CUT"}
        if motion_enabled:
            scene.generation_config = {"motion_context": {"enabled": True}}
        if boundaries:
            scene.spec = {"continuity": boundaries[0]}
        scenes = [scene]
        for index in range(1, count):
            scene = Scene(
                video_id=video.id,
                scene_order=index,
                prompt=f"Member {index}",
                duration_seconds=5,
                spec={"continuity": boundaries[index] if boundaries else "CONTINUOUS"},
            )
            session.add(scene)
            scenes.append(scene)
        await session.flush()
        require_contract(workflow, approved)
        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        generations = [
            await service.create(
                session,
                scene_id=scene.id,
                request=GenerationRequest(mode="t2v", quality_profile="BASE", seed=42),
                user_id=user_id,
                request_id="dynamic-test",
                defer_aggregate_qualification=True,
                persist=persist,
            )
            for scene in scenes
        ]
        await session.flush()
        return (
            user_id,
            video.id,
            [scene.id for scene in scenes],
            [g.id for g in generations] if persist else generations,
        )


async def qualify_run(session, generations, scenes, video, user_id, monkeypatch):
    """Build a synthetic measured case without bypassing the gate in the actual assertion."""
    workflow = await session.get(WorkflowRecord, generations[0].workflow_id)
    captured = {}

    def synthetic_case(profile, spec, *, member_count, continuities):
        settings = runs.director_aggregate_settings(
            spec,
            member_count=member_count,
            continuities=continuities,
            output_artifacts=profile["output_artifacts"],
        )
        captured.update(copy.deepcopy(profile["execution_evidence"]))
        captured.update(
            member_count=member_count,
            continuities=list(continuities),
            task=spec.task,
            settings_hash=stable_hash(settings),
            settings=settings,
        )
        return copy.deepcopy(captured)

    with monkeypatch.context() as setup:
        setup.setattr(runs, "require_director_aggregate_qualification", synthetic_case)
        await runs.create_director_run(
            session, generations, scenes, video=video, user_id=user_id, persist=False
        )
    profile = copy.deepcopy(workflow.profile)
    profile["execution_evidence"]["director_aggregate_cases"] = [captured]
    workflow.profile = profile
    # Evidence is excluded from profile identity, but freeze it into each member exactly
    # as a real qualification import would precede generation creation.
    for generation in generations:
        snapshot = copy.deepcopy(generation.input_snapshot)
        snapshot["runtime_profile"] = profile
        spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
        values = spec.model_dump(mode="json", exclude={"execution_hash"})
        values["provenance"]["workflow_profile"] = profile
        snapshot["director_execution"] = DirectorExecutionSpec.finalize(**values).model_dump(
            mode="json"
        )
        snapshot["semantic_hash"] = stable_hash(
            {key: value for key, value in snapshot.items() if key != "semantic_hash"}
        )
        generation.input_snapshot = snapshot
    await session.flush()


@pytest.mark.parametrize("count", [1, 2, 3, 5])
async def test_studio_service_freezes_dynamic_run_and_persists_preflight_exactly(
    session_factory, tmp_path, monkeypatch, count
):
    from apps.api.app.db.models import SceneGeneration

    user_id, video_id, scene_ids, ids = await prepared_members(session_factory, tmp_path, count)
    async with session_factory() as session, session.begin():
        generations = [await session.get(SceneGeneration, key) for key in ids]
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        for generation in generations:
            assert generation.input_snapshot["provider"] == "minimax_h3_director"
            assert generation.input_snapshot["generation_intent"]["provider_task"] == "t2v"
            assert (
                generation.input_snapshot["director_execution"]["provider"] == "minimax_h3_director"
            )
        capabilities = await generation_capabilities(
            user=await session.get(User, user_id), session=session
        )
        assert capabilities.combinations
        assert all(case["provider"] == "minimax_h3_director" for case in capabilities.combinations)
        await qualify_run(session, generations, scenes, video, user_id, monkeypatch)
        run = await runs.create_director_run(
            session, generations, scenes, video=video, user_id=user_id, persist=False
        )
        assert not session.new
        assert await session.scalar(select(func.count()).select_from(DirectorRun)) == 0
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == 0
        frozen = copy.deepcopy(run.input_snapshot)
        spec = DirectorExecutionSpec.model_validate(frozen["director_execution"])
        assert spec.member_count == count
        history = {
            "outputs": {
                frozen["output_node"]: {
                    "studio_director_artifacts": [
                        {
                            "role": "segment",
                            "member_index": index,
                            "filename": f"member-{index}.mp4",
                            "type": "output",
                        }
                        for index in reversed(range(count))
                    ]
                }
            }
        }
        assert [
            item.member_index
            for item in artifact_manifest(
                history,
                spec.provenance["workflow_profile"],
                require_final=False,
                member_count=spec.member_count,
            )
        ] == list(range(count))
        graph = DirectorWorkflowBuilder().build(
            base_workflow=frozen["base_workflow"], spec=spec, staged_assets={}
        )
        director = next(
            node for node in graph.values() if node["class_type"] == "StudioMiniMaxH3Director"
        )
        assert len(json.loads(director["inputs"]["timeline_data"])["segments"]) == count
        from tests.integration.test_director_dispatcher import worker

        async def object_info():
            return {
                node["class_type"]: {
                    "input": {"required": {name: ["ANY"] for name in node["inputs"]}}
                }
                for node in frozen["base_workflow"].values()
            }

        dispatched = await worker(
            session_factory, tmp_path, SimpleNamespace(object_info=object_info)
        )._prepare_workflow(frozen)
        dispatched_director = next(
            node for node in dispatched.values() if node["class_type"] == "StudioMiniMaxH3Director"
        )
        assert len(json.loads(dispatched_director["inputs"]["timeline_data"])["segments"]) == count
        if count == 3:
            for field in ("frames", "duration_seconds", "member_count", "base_workflow", "members"):
                changed = copy.deepcopy(frozen)
                if field == "base_workflow":
                    changed[field]["5"]["inputs"]["steps"] = 26
                elif field == "members":
                    changed[field][1]["member_index"] = 0
                else:
                    changed[field] += 1
                changed["semantic_hash"] = stable_hash(
                    {key: value for key, value in changed.items() if key != "semantic_hash"}
                )
                with pytest.raises(AppError):
                    await worker(
                        session_factory, tmp_path, SimpleNamespace(object_info=object_info)
                    )._prepare_workflow(changed)
        await runs.persist_prepared_director_run(session, run)
        assert run.input_snapshot == frozen
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == count
        assert await session.get(DirectorRun, run.id) is run


async def test_dynamic_graph_does_not_qualify_unexecuted_count(session_factory, tmp_path):
    from apps.api.app.db.models import SceneGeneration

    user_id, video_id, scene_ids, ids = await prepared_members(session_factory, tmp_path, 3)
    async with session_factory() as session, session.begin():
        generations = [await session.get(SceneGeneration, key) for key in ids]
        scenes = [await session.get(Scene, key) for key in scene_ids]
        with pytest.raises(AppError) as error:
            await runs.create_director_run(
                session,
                generations,
                scenes,
                video=await session.get(Video, video_id),
                user_id=user_id,
                persist=False,
            )
        assert error.value.code == "DIRECTOR_AGGREGATE_NOT_QUALIFIED"
        assert error.value.details["member_count"] == 3
        assert not session.new
        assert await session.scalar(select(func.count()).select_from(DirectorRun)) == 0


async def test_new_run_rejects_historical_static_binding(session_factory, tmp_path):
    user_id, video_id, scene_ids, generations = await prepared_members(
        session_factory, tmp_path, 2, persist=False
    )
    async with session_factory() as session, session.begin():
        workflow = await session.get(WorkflowRecord, generations[0].workflow_id)
        profile = copy.deepcopy(workflow.profile)
        binding = profile["output_artifacts"][0]
        binding.pop("coverage")
        binding.update(member_index=0, count=2)
        workflow.profile = profile
        with pytest.raises(AppError, match="dynamic"):
            await runs.create_director_run(
                session,
                generations,
                [await session.get(Scene, key) for key in scene_ids],
                video=await session.get(Video, video_id),
                user_id=user_id,
                persist=False,
            )
        assert not session.new


async def qualified_batch_state(
    factory, tmp_path, monkeypatch, boundaries, *, motion_enabled=False
):
    user_id, video_id, scene_ids, generations = await prepared_members(
        factory,
        tmp_path,
        len(boundaries),
        persist=False,
        boundaries=boundaries,
        motion_enabled=motion_enabled,
    )
    async with factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        starts = [
            0,
            *[index for index in range(1, len(scenes)) if boundaries[index] == "CUT"],
            len(scenes),
        ]
        cases = []
        for start, end in zip(starts, starts[1:], strict=False):
            await qualify_run(
                session, generations[start:end], scenes[start:end], video, user_id, monkeypatch
            )
            workflow = await session.get(WorkflowRecord, generations[start].workflow_id)
            cases.extend(
                copy.deepcopy(workflow.profile["execution_evidence"]["director_aggregate_cases"])
            )
        profile = copy.deepcopy(workflow.profile)
        profile["execution_evidence"]["director_aggregate_cases"] = cases
        workflow.profile = profile
    return user_id, video_id, scene_ids


async def submit_batch(factory, tmp_path, user_id, video_id):
    async with factory() as session, session.begin():
        return await generate_all(
            video_id=video_id,
            payload=GenerateAll(settings=GenerationRequest(quality_profile="BASE", seed=42)),
            request=SimpleNamespace(state=SimpleNamespace(request_id="dynamic-batch")),
            key="dynamic-batch",
            user=await session.get(User, user_id),
            session=session,
            settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
        )


async def test_generate_all_three_member_native_chain(session_factory, tmp_path, monkeypatch):
    user_id, video_id, scene_ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CONTINUOUS"]
    )
    result = await submit_batch(session_factory, tmp_path, user_id, video_id)
    assert len(result.generations) == 3
    assert len(result.execution_groups) == 1
    assert [str(key) for key in result.execution_groups[0].scene_ids] == scene_ids
    async with session_factory() as session:
        run = await session.scalar(select(DirectorRun))
        assert run.input_snapshot["member_count"] == 3
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == 3


async def test_generate_all_two_native_chains(session_factory, tmp_path, monkeypatch):
    user_id, video_id, _ = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CUT", "CONTINUOUS"]
    )
    result = await submit_batch(session_factory, tmp_path, user_id, video_id)
    assert len(result.execution_groups) == 2
    assert all(group.execution_scope == "aggregate" for group in result.execution_groups)
    assert result.director_run_id is None
    async with session_factory() as session:
        runs = list((await session.scalars(select(DirectorRun))).all())
        assert len(runs) == 2
        assert [run.input_snapshot["member_count"] for run in runs] == [2, 2]
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == 4


async def test_generate_all_one_member_motion_context_uses_native_aggregate(
    session_factory, tmp_path, monkeypatch
):
    user_id, video_id, _ = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT"], motion_enabled=True
    )
    result = await submit_batch(session_factory, tmp_path, user_id, video_id)
    assert result.execution_groups[0].execution_scope == "aggregate"
    async with session_factory() as session:
        run = await session.scalar(select(DirectorRun))
        assert run.input_snapshot["director_execution"]["member_count"] == 1
        assert run.input_snapshot["director_execution"]["motion_context"]["enabled"] is True


async def test_generate_all_native_chain_and_reference_scene_are_separate(
    session_factory, tmp_path, monkeypatch
):
    user_id, video_id, scene_ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CONTINUOUS"]
    )
    async with session_factory() as session, session.begin():
        video = await session.get(Video, video_id)
        aggregate = await session.scalar(select(WorkflowRecord))
        directory = DIRECTORY
        entry = next(
            item
            for item in json.loads((directory / "registry.json").read_text())["workflows"]
            if item["code"] == "H3_DIRECTOR_R2V_BASE"
        )
        graph = json.loads((directory / entry["file"]).read_text())
        approved = ApprovedWorkflow(
            "r2v",
            entry["version"],
            graph,
            {key: tuple(value) for key, value in entry["slots"].items()},
        )
        profile = copy.deepcopy(entry["profile"])
        profile["weight_hashes"] = {value: "a" * 64 for value in collect_executable_weights(graph)}
        profile["dependency_versions"] = {
            node["class_type"]: "synthetic-test-only" for node in graph.values()
        }
        evidence = copy.deepcopy(aggregate.profile["execution_evidence"])
        evidence.pop("director_aggregate_cases", None)
        evidence.update(
            workflow_hash=approved.workflow_hash,
            slot_map_hash=approved.slot_map_hash,
            dependency_versions=profile["dependency_versions"],
            weight_hashes=profile["weight_hashes"],
            profile_hash=profile_hash(profile),
        )
        evidence["combinations"][0]["mode"] = "r2v"
        profile.update(poc_verified=True, execution_evidence=evidence)
        session.add(
            WorkflowRecord(
                code=entry["code"],
                mode="r2v",
                version=entry["version"],
                quality_profile="BASE",
                execution_scope="single_scene",
                workflow=graph,
                slots=entry["slots"],
                required_slots=[],
                profile=profile,
                enabled=True,
                workflow_hash=approved.workflow_hash,
                slot_map_hash=approved.slot_map_hash,
                created_by=user_id,
            )
        )
        asset = Asset(
            project_id=video.project_id,
            kind="IMAGE",
            role="REFERENCE_IMAGE",
            filename="reference.png",
            content_type="image/png",
            object_key="test/reference.png",
            status="READY",
            width=864,
            height=480,
            checksum="a" * 64,
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        scene = Scene(
            video_id=video_id,
            scene_order=3,
            prompt="Reference scene",
            duration_seconds=5,
            spec={"continuity": "CUT"},
            generation_config={"mode": "r2v", "reference_image_asset_ids": [asset.id]},
        )
        session.add(scene)
        await session.flush()
        reference_id = scene.id
    result = await submit_batch(session_factory, tmp_path, user_id, video_id)
    assert len(result.generations) == 4
    assert len(result.execution_groups) == 2
    aggregate_group, reference_group = result.execution_groups
    assert aggregate_group.execution_scope == "aggregate"
    assert [str(key) for key in aggregate_group.scene_ids] == scene_ids
    assert reference_group.execution_scope == "single_scene"
    assert [str(key) for key in reference_group.scene_ids] == [reference_id]
    assert reference_group.director_run_id is None
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(DirectorRun)) == 1
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == 3
        reference = await session.scalar(
            select(SceneGeneration).where(SceneGeneration.scene_id == reference_id)
        )
        assert reference.mode == "r2v"


async def test_generate_all_incompatible_continuity_persists_no_jobs(
    session_factory, tmp_path, monkeypatch
):
    user_id, video_id, scene_ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CONTINUOUS"]
    )
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_ids[1])
        scene.generation_config = {"cfg": 2.0}
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError):
            await generate_all(
                video_id=video_id,
                payload=GenerateAll(settings=GenerationRequest(quality_profile="BASE", seed=42)),
                request=SimpleNamespace(state=SimpleNamespace(request_id="incompatible-batch")),
                key="incompatible-batch",
                user=await session.get(User, user_id),
                session=session,
                settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
            )
        # Check before rollback: no partially prepared job was added or flushed.
        assert not any(
            isinstance(row, (SceneGeneration, DirectorRun, DirectorRunMember))
            for row in session.new
        )
        for model in (SceneGeneration, DirectorRun, DirectorRunMember):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


async def test_dispatcher_completion_rejects_duplicate_member_before_publishing(
    session_factory, tmp_path
):
    from tests.integration.test_director_dispatcher import seed_run, worker

    run_id, ids = await seed_run(session_factory)
    dispatcher = worker(session_factory, tmp_path)
    await dispatcher.claim()
    outputs = [
        {"role": "segment", "member_index": index, "asset_id": "not-published"}
        for index in (0, 1, 1)
    ]
    with pytest.raises(ValueError, match="DIRECTOR_SEGMENT_COVERAGE_INVALID"):
        await dispatcher._finish(run_id, "COMPLETED", artifacts=outputs)
    async with session_factory() as session:
        assert (await session.get(DirectorRun, run_id)).status == "DISPATCHING"
        for key in ids:
            assert (await session.get(SceneGeneration, key)).status == "DISPATCHING"


@pytest.mark.parametrize(
    "field",
    [
        "member_count",
        "continuities",
        "settings_hash",
        "workflow_hash",
        "slot_map_hash",
        "profile_hash",
        "model_hash",
        "weight_hashes",
        "dependency_versions",
        "custom_node_versions",
        "comfyui_commit",
    ],
)
async def test_aggregate_evidence_rejects_count_settings_and_provenance_changes(
    session_factory, tmp_path, monkeypatch, field
):
    user_id, video_id, scene_ids = await qualified_batch_state(
        session_factory, tmp_path, monkeypatch, ["CUT", "CONTINUOUS", "CONTINUOUS"]
    )
    async with session_factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        profile = copy.deepcopy(workflow.profile)
        case = profile["execution_evidence"]["director_aggregate_cases"][0]
        if field == "member_count":
            case[field] = 5
        elif field == "continuities":
            case[field] = ["CUT"] * 3
        elif isinstance(case[field], dict):
            case[field] = {"changed": "a" * 64}
        else:
            case[field] = "b" * (40 if field == "comfyui_commit" else 64)
        workflow.profile = profile
    with pytest.raises(AppError) as error:
        await submit_batch(session_factory, tmp_path, user_id, video_id)
    assert error.value.code == "DIRECTOR_AGGREGATE_NOT_QUALIFIED"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0
        assert await session.scalar(select(func.count()).select_from(DirectorRun)) == 0


@pytest.mark.parametrize("change", ["missing_count", "source_repository", "source_commit"])
async def test_dynamic_qualification_requires_frozen_count_and_pinned_source(
    session_factory, tmp_path, monkeypatch, change
):
    user_id, video_id, scene_ids, generations = await prepared_members(
        session_factory, tmp_path, 3, persist=False
    )
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        await qualify_run(session, generations, scenes, video, user_id, monkeypatch)
        run = await runs.create_director_run(
            session, generations, scenes, video=video, user_id=user_id, persist=False
        )
        values = run.input_snapshot["director_execution"].copy()
        values["member_count" if change == "missing_count" else change] = (
            None if change == "missing_count" else "unqualified-source"
        )
        spec = DirectorExecutionSpec.finalize(**values)
        workflow = await session.get(WorkflowRecord, run.workflow_id)
        # Even coherent forged settings cannot substitute a different source identity.
        profile = copy.deepcopy(workflow.profile)
        case = profile["execution_evidence"]["director_aggregate_cases"][0]
        settings = runs.director_aggregate_settings(
            spec,
            member_count=3,
            continuities=run.input_snapshot["continuities"],
            output_artifacts=profile["output_artifacts"],
        )
        case.update(settings=settings, settings_hash=stable_hash(settings))
        with pytest.raises(AppError):
            runs.require_director_aggregate_qualification(
                profile, spec, member_count=3, continuities=run.input_snapshot["continuities"]
            )
