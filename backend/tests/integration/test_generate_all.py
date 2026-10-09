from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from apps.api.app.api.generations import generate_all
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Scene, SceneGeneration, User, Video, WorkflowRecord
from apps.api.app.schemas.api import GenerateAll, GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import director_workflow_data, seed


async def submit(factory, tmp_path, user_id, video_id, payload, key="batch-1"):
    async with factory() as session, session.begin():
        return await generate_all(
            video_id=video_id,
            payload=payload,
            request=SimpleNamespace(state=SimpleNamespace(request_id="batch-test")),
            key=key,
            user=await session.get(User, user_id),
            session=session,
            settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
        )


async def batch_state(factory):
    user_id, scene_id = await seed(factory)
    async with factory() as session:
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        return user_id, scene_id, video.id, video.revision, scene.revision


@pytest.mark.asyncio
async def test_all_groups_validate_before_any_job_is_persisted(session_factory, tmp_path):
    user, first, video, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        session.add(
            Scene(
                video_id=video,
                scene_order=1,
                prompt="Invalid later CUT",
                duration_seconds=5,
                generation_config={"cfg": 29},
            )
        )
    # Catch inside the transaction: rollback must not hide premature job inserts.
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as error:
            await generate_all(
                video_id=video,
                payload=GenerateAll(),
                request=SimpleNamespace(state=SimpleNamespace(request_id="preflight")),
                key="preflight",
                user=await session.get(User, user),
                session=session,
                settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
            )
        assert error.value.code == "GENERATION_INPUT_INVALID"
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0
        assert not any(isinstance(row, SceneGeneration) for row in session.new)


@pytest.mark.asyncio
async def test_prepared_generation_cannot_change_between_planning_and_persistence(
    session_factory,
    tmp_path,
):
    user, scene, video, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(Settings(_env_file=None, workspace_root=tmp_path))
        prepared = await service.create(
            session,
            scene_id=scene,
            request=GenerationRequest(seed=42),
            user_id=user,
            request_id=None,
            persist=False,
        )
        assert not any(isinstance(row, SceneGeneration) for row in session.new)
        prepared.input_snapshot["prompt"] = "Changed after validation"
        with pytest.raises(AppError) as error:
            await service.persist_prepared(session, prepared, await session.get(Video, video))
        assert error.value.code == "GENERATION_INPUT_INVALID"
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0


@pytest.mark.asyncio
async def test_cut_groups_are_reported_in_video_order_and_replayed(session_factory, tmp_path):
    user, first, video, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        second = Scene(video_id=video, scene_order=1, prompt="Separate CUT", duration_seconds=5)
        session.add(second)
        await session.flush()
        second_id = second.id
    payload = GenerateAll(scene_ids=[second_id, first])
    result = await submit(session_factory, tmp_path, user, video, payload)
    assert [group.scene_ids for group in result.execution_groups] == [[first], [second_id]]
    assert all(group.execution_scope == "single_scene" for group in result.execution_groups)
    assert result.director_run_id is None
    assert await submit(session_factory, tmp_path, user, video, payload) == result


@pytest.mark.asyncio
async def test_three_cut_scenes_respect_different_saved_tasks(session_factory, tmp_path):
    user, first, video, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        parent = await session.get(Video, video)
        asset = Asset(
            project_id=parent.project_id,
            kind="IMAGE",
            role="REFERENCE",
            filename="frame.png",
            content_type="image/png",
            status="READY",
            size_bytes=4,
            object_key="test/frame",
            checksum="a" * 64,
            width=32,
            height=32,
            created_by=user,
        )
        session.add(asset)
        last = Asset(
            project_id=parent.project_id,
            kind="IMAGE",
            role="REFERENCE",
            filename="last.png",
            content_type="image/png",
            status="READY",
            size_bytes=4,
            object_key="test/last",
            checksum="b" * 64,
            width=32,
            height=32,
            created_by=user,
        )
        session.add(last)
        await session.flush()
        for index, mode in enumerate(["r2v", "i2v_first_last"], 1):
            task = "fl2v" if mode == "i2v_first_last" else mode
            graph, slots, profile, approved = director_workflow_data(mode=task)
            session.add(
                WorkflowRecord(
                    code=f"TEST_{mode}",
                    mode=task,
                    version="1",
                    workflow=graph,
                    slots=slots,
                    required_slots=list(slots),
                    profile=profile,
                    workflow_hash=approved.workflow_hash,
                    slot_map_hash=approved.slot_map_hash,
                    enabled=True,
                    created_by=user,
                )
            )
            config = {"mode": mode}
            if mode == "r2v":
                config["reference_image_asset_ids"] = [asset.id]
            else:
                config.update(first_frame_asset_id=asset.id, last_frame_asset_id=last.id)
            session.add(
                Scene(
                    video_id=video,
                    scene_order=index,
                    prompt=f"CUT {mode}",
                    duration_seconds=5,
                    generation_config=config,
                )
            )
    result = await submit(session_factory, tmp_path, user, video, GenerateAll())
    assert [row.mode for row in result.generations] == ["t2v", "r2v", "i2v_first_last"]
    assert len(result.execution_groups) == 3
    assert all(group.execution_scope == "single_scene" for group in result.execution_groups)


@pytest.mark.asyncio
async def test_continuous_member_requires_selected_immediate_predecessor(session_factory, tmp_path):
    user, first, video, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        second = Scene(
            video_id=video,
            scene_order=1,
            prompt="Continuous continuation",
            duration_seconds=5,
            spec={"continuity": "CONTINUOUS"},
        )
        session.add(second)
        await session.flush()
        second_id = second.id
    with pytest.raises(AppError) as error:
        await submit(session_factory, tmp_path, user, video, GenerateAll(scene_ids=[second_id]))
    assert error.value.code == "DIRECTOR_CONTINUITY_CHAIN_REQUIRED"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0


@pytest.mark.asyncio
async def test_lost_response_replays_without_duplicate_generations(session_factory, tmp_path):
    user, scene, video, video_rev, scene_rev = await batch_state(session_factory)
    payload = GenerateAll(
        scene_ids=[scene],
        expected_video_revision=video_rev,
        expected_scene_revisions={scene: scene_rev},
    )
    first = await submit(session_factory, tmp_path, user, video, payload)
    retry = await submit(session_factory, tmp_path, user, video, payload)
    assert retry == first
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 1


@pytest.mark.asyncio
async def test_legacy_key_cannot_replay_after_scene_semantics_change(session_factory, tmp_path):
    user, scene, video, _, _ = await batch_state(session_factory)
    await submit(session_factory, tmp_path, user, video, GenerateAll())
    async with session_factory() as session, session.begin():
        row = await session.get(Scene, scene)
        row.prompt = "Changed bottle scene"
        row.revision += 1
    with pytest.raises(AppError) as error:
        await submit(session_factory, tmp_path, user, video, GenerateAll())
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["spec", "video_config"])
async def test_legacy_key_detects_configuration_change_without_revision_bump(
    session_factory, tmp_path, change
):
    user, scene, video, _, _ = await batch_state(session_factory)
    await submit(session_factory, tmp_path, user, video, GenerateAll())
    async with session_factory() as session, session.begin():
        if change == "spec":
            row = await session.get(Scene, scene)
            row.spec = {**row.spec, "camera": "close up"}
        else:
            row = await session.get(Video, video)
            row.config = {**row.config, "prompt_enhancer": "changed"}
    with pytest.raises(AppError) as error:
        await submit(session_factory, tmp_path, user, video, GenerateAll())
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["video", "scene", "added", "disabled"])
async def test_changed_batch_rejects_stale_preconditions(session_factory, tmp_path, change):
    user, scene, video, video_rev, scene_rev = await batch_state(session_factory)
    payload = GenerateAll(
        expected_video_revision=video_rev,
        expected_scene_revisions={scene: scene_rev},
    )
    async with session_factory() as session, session.begin():
        row = await session.get(Scene, scene)
        parent = await session.get(Video, video)
        if change == "scene":
            row.revision += 1
        elif change == "disabled":
            row.enabled = False
            parent.revision += 1
        elif change == "added":
            session.add(Scene(video_id=video, scene_order=1, prompt="Added", spec={}))
            parent.revision += 1
        else:
            parent.revision += 1
    with pytest.raises(AppError) as error:
        await submit(session_factory, tmp_path, user, video, payload)
    assert error.value.code == "REVISION_CONFLICT"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 0


@pytest.mark.asyncio
async def test_changed_scene_with_new_key_creates_new_generation(session_factory, tmp_path):
    user, scene, video, video_rev, scene_rev = await batch_state(session_factory)
    await submit(session_factory, tmp_path, user, video, GenerateAll())
    async with session_factory() as session, session.begin():
        row = await session.get(Scene, scene)
        row.prompt = "New semantic request"
        row.revision += 1
    response = await submit(
        session_factory,
        tmp_path,
        user,
        video,
        GenerateAll(
            scene_ids=[scene],
            expected_video_revision=video_rev,
            expected_scene_revisions={scene: scene_rev + 1},
        ),
        key="batch-2",
    )
    assert response.generations[0].generation_no == 2


def test_batch_preconditions_must_be_paired_and_positive():
    for values in [
        {"expected_video_revision": 1},
        {"expected_scene_revisions": {UUID(int=1): 1}},
        {"expected_video_revision": 1, "expected_scene_revisions": {UUID(int=1): 0}},
    ]:
        with pytest.raises(ValidationError):
            GenerateAll(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_boundary", ["CUT", "CONTINUOUS"])
async def test_disabled_predecessor_is_boundary_in_actual_generate_all(
    session_factory, tmp_path, disabled_boundary
):
    from apps.api.app.db.models import DirectorRun, DirectorRunMember

    user, first, video_id, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        predecessor = await session.get(Scene, first)
        predecessor.enabled = False
        predecessor.spec = {"continuity": disabled_boundary}
        successor = Scene(
            video_id=video_id,
            scene_order=1,
            prompt="New enabled boundary",
            duration_seconds=5,
            spec={"continuity": "CONTINUOUS"},
        )
        session.add(successor)
        await session.flush()
        successor_id = successor.id
    response = await submit(session_factory, tmp_path, user, video_id, GenerateAll())
    assert [str(row.scene_id) for row in response.generations] == [successor_id]
    assert [group.scene_ids for group in response.execution_groups] == [[successor_id]]
    assert response.execution_groups[0].execution_scope == "single_scene"
    async with session_factory() as session:
        assert (await session.get(Scene, first)).enabled is False
        generation = await session.get(SceneGeneration, str(response.generations[0].id))
        inputs = generation.input_snapshot["generation_freshness"]["inputs"]
        assert [member["id"] for member in inputs["source_group"]] == [successor_id]
        assert await session.scalar(select(func.count()).select_from(DirectorRun)) == 0
        assert await session.scalar(select(func.count()).select_from(DirectorRunMember)) == 0


@pytest.mark.asyncio
async def test_all_disabled_rejection_has_no_jobs_before_rollback(session_factory, tmp_path):
    from apps.api.app.db.models import DirectorRun, DirectorRunMember

    user, first, video_id, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        (await session.get(Scene, first)).enabled = False
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as caught:
            await generate_all(
                video_id=video_id,
                payload=GenerateAll(),
                request=SimpleNamespace(state=SimpleNamespace(request_id="disabled")),
                key="disabled",
                user=await session.get(User, user),
                session=session,
                settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
            )
        assert caught.value.code == "NO_SCENES_TO_GENERATE"
        await session.flush()
        for model in (SceneGeneration, DirectorRun, DirectorRunMember):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_index", [0, 1])
async def test_partial_chain_rejection_has_no_jobs_inside_transaction(
    session_factory, tmp_path, selected_index
):
    from apps.api.app.db.models import DirectorRun, DirectorRunMember

    user, first, video_id, _, _ = await batch_state(session_factory)
    async with session_factory() as session, session.begin():
        second = Scene(
            video_id=video_id,
            scene_order=1,
            prompt="Native successor",
            duration_seconds=5,
            spec={"continuity": "CONTINUOUS"},
        )
        session.add(second)
        await session.flush()
        identities = [first, second.id]
    async with session_factory() as session, session.begin():
        with pytest.raises(AppError) as caught:
            await generate_all(
                video_id=video_id,
                payload=GenerateAll(scene_ids=[identities[selected_index]]),
                request=SimpleNamespace(state=SimpleNamespace(request_id="partial")),
                key="partial",
                user=await session.get(User, user),
                session=session,
                settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
            )
        assert caught.value.code == "DIRECTOR_CONTINUITY_CHAIN_REQUIRED"
        assert caught.value.details["required_scene_ids"] == identities
        await session.flush()
        for model in (SceneGeneration, DirectorRun, DirectorRunMember):
            assert await session.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("motion_enabled", [False, True])
@pytest.mark.parametrize("qualified", [False, True])
async def test_disabled_prefix_preserves_aggregate_qualification_and_snapshots(
    session_factory, tmp_path, monkeypatch, motion_enabled, qualified
):
    import copy

    from apps.api.app.db.models import DirectorRun, DirectorRunMember
    from apps.api.app.services.continuity_groups import continuity_chains
    from tests.integration.test_director_dynamic_aggregate import qualified_batch_state

    count = 1 if motion_enabled else 2
    user_id, video_id, scene_ids = await qualified_batch_state(
        session_factory,
        tmp_path,
        monkeypatch,
        ["CONTINUOUS"] * count,
        motion_enabled=motion_enabled,
    )
    async with session_factory() as session, session.begin():
        for index, identity in enumerate(scene_ids):
            (await session.get(Scene, identity)).scene_order = index + 2
        await session.flush()
        session.add_all(
            [
                Scene(
                    video_id=video_id,
                    scene_order=index,
                    prompt="Disabled prefix",
                    duration_seconds=5,
                    enabled=False,
                    spec={"continuity": "CONTINUOUS"},
                )
                for index in range(2)
            ]
        )
        if not qualified:
            workflow = await session.scalar(select(WorkflowRecord))
            profile = copy.deepcopy(workflow.profile)
            profile["execution_evidence"].pop("director_aggregate_cases", None)
            workflow.profile = profile

    async with session_factory() as session, session.begin():
        rows = list((await session.scalars(select(Scene).where(Scene.video_id == video_id))).all())
        assert [[row.id for row in chain] for chain in continuity_chains(rows)] == [scene_ids]
        arguments = dict(
            video_id=video_id,
            payload=GenerateAll(settings=GenerationRequest(quality_profile="BASE", seed=42)),
            request=SimpleNamespace(state=SimpleNamespace(request_id="disabled-aggregate")),
            key="disabled-aggregate",
            user=await session.get(User, user_id),
            session=session,
            settings=Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0),
        )
        if not qualified:
            with pytest.raises(AppError) as caught:
                await generate_all(**arguments)
            assert caught.value.code == "DIRECTOR_AGGREGATE_NOT_QUALIFIED"
            await session.flush()
            for model in (SceneGeneration, DirectorRun, DirectorRunMember):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
            return
        response = await generate_all(**arguments)
        assert len(response.execution_groups) == 1
        group = response.execution_groups[0]
        assert group.scene_ids == scene_ids
        assert group.execution_scope == "aggregate"
        run = await session.get(DirectorRun, str(group.director_run_id))
        assert run.input_snapshot["member_count"] == count
        assert [member["scene_id"] for member in run.input_snapshot["members"]] == scene_ids
        assert all(not row.enabled for row in rows if row.scene_order < 2)
        members = list(
            (
                await session.scalars(
                    select(DirectorRunMember)
                    .where(DirectorRunMember.director_run_id == run.id)
                    .order_by(DirectorRunMember.member_index)
                )
            ).all()
        )
        assert [member.scene_id for member in members] == scene_ids
        segments = run.input_snapshot["director_execution"]["timeline"]
        assert segments[0]["continuity_from_previous"] is False
        assert [segment["continuity_from_previous"] for segment in segments[1:]] == [True] * (
            count - 1
        )
