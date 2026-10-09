"""Native chain sources and run association must remain coherent end to end."""

import copy
import hashlib

import pytest

from apps.api.app.api.scenes import select_generation
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, DirectorRun, Scene, SceneGeneration, User, Video
from apps.api.app.schemas.api import AssemblyRequest, GenerateAll, GenerationRequest, Selection
from apps.api.app.services.assembly_service import AssemblyService
from apps.api.app.services.generation_freshness import (
    bind_execution_group_freshness,
    is_assembly_source_current,
    is_generation_fresh,
    is_selected_generation_fresh,
)
from apps.api.app.services.generation_intent import stable_hash
from tests.integration.test_director_dynamic_aggregate import (
    prepared_members,
    qualified_batch_state,
    qualify_run,
    submit_batch,
)
from tests.integration.test_generate_all import submit
from workers.director_dispatcher import DirectorDispatcher


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field", ["prompt", "negative_prompt", "duration_seconds", "spec", "generation_config"]
)
async def test_upstream_edit_stales_downstream_native_source(session_factory, tmp_path, field):
    _, video_id, scene_ids, ids = await prepared_members(session_factory, tmp_path, 2)
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        generations = [await session.get(SceneGeneration, key) for key in ids]
        video = await session.get(Video, video_id)
        assert await is_generation_fresh(session, scenes[1], video, generations[1])
        setattr(
            scenes[0],
            field,
            {"cfg": 2, "frames": 121, "seed": 9}
            if field == "generation_config"
            else {"continuity": "CUT", "camera": "different"}
            if field == "spec"
            else 6
            if field == "duration_seconds"
            else "Edited native predecessor",
        )
        assert not await is_generation_fresh(session, scenes[1], video, generations[1])


async def complete_run(factory, tmp_path, run_id):
    dispatcher = DirectorDispatcher(
        factory,
        None,
        None,
        None,
        Settings(
            _env_file=None,
            workspace_root=tmp_path,
            min_free_disk_bytes=0,
        ),
    )
    assert await dispatcher.claim() == run_id
    async with factory() as session, session.begin():
        run = await session.get(DirectorRun, run_id)
        artifacts = []
        for member in run.input_snapshot["members"]:
            asset = Asset(
                kind="VIDEO",
                filename="clip.mp4",
                content_type="video/mp4",
                object_key=f"{run.id}/{member['member_index']}.mp4",
                status="READY",
                checksum=hashlib.sha256(b"clip").hexdigest(),
                size_bytes=4,
                created_by=run.created_by,
            )
            session.add(asset)
            await session.flush()
            artifacts.append(
                {"role": "segment", "member_index": member["member_index"], "asset_id": asset.id}
            )
    await dispatcher._finish(run_id, "COMPLETED", artifacts=artifacts)


async def selected_chain(factory, tmp_path, monkeypatch):
    user, video_id, scene_ids = await qualified_batch_state(
        factory,
        tmp_path,
        monkeypatch,
        ["CUT", "CONTINUOUS"],
    )
    result = await submit_batch(factory, tmp_path, user, video_id)
    run_id = str(result.execution_groups[0].director_run_id)
    await complete_run(factory, tmp_path, run_id)
    return user, video_id, scene_ids, [row.id for row in result.generations], run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("edited", [0, 1])
async def test_edit_either_member_stales_entire_selected_chain_and_generates_full_group(
    session_factory,
    tmp_path,
    monkeypatch,
    edited,
):
    user, video_id, scene_ids, ids, _ = await selected_chain(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        assert all([await is_selected_generation_fresh(session, scene, video) for scene in scenes])
        scenes[edited].prompt = "Edited native member"
        scenes[edited].revision += 1
        video.revision += 1
        assert not any(
            [await is_selected_generation_fresh(session, scene, video) for scene in scenes]
        )
        assert [scene.selected_generation_id for scene in scenes] == ids
        with pytest.raises(AppError) as error:
            await AssemblyService().create(
                session,
                video_id=video_id,
                request=AssemblyRequest(),
                expected_revision=video.revision,
                user_id=user,
                request_id=None,
            )
        assert error.value.code == "SCENE_SELECTION_STALE"
    result = await submit(
        session_factory,
        tmp_path,
        user,
        video_id,
        GenerateAll(settings=GenerationRequest(quality_profile="BASE", seed=42)),
        key="regenerate-native-chain",
    )
    assert [row.scene_id for row in result.generations] == scene_ids
    assert len(result.execution_groups) == 1
    assert result.execution_groups[0].execution_scope == "aggregate"


@pytest.mark.asyncio
async def test_identical_semantics_from_different_runs_cannot_mix_selected_outputs(
    session_factory,
    tmp_path,
    monkeypatch,
):
    user, video_id, scene_ids, old_ids, _ = await selected_chain(
        session_factory, tmp_path, monkeypatch
    )
    newer = await submit(
        session_factory,
        tmp_path,
        user,
        video_id,
        GenerateAll(
            scene_ids=scene_ids, settings=GenerationRequest(quality_profile="BASE", seed=42)
        ),
        key="another-identical-run",
    )
    await complete_run(session_factory, tmp_path, str(newer.execution_groups[0].director_run_id))
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        assert [scene.selected_generation_id for scene in scenes] == old_ids
        first = await select_generation(
            scene_ids[0],
            Selection(generation_id=newer.generations[0].id),
            revision=scenes[0].revision,
            user=await session.get(User, user),
            session=session,
        )
        assert not first.selected_generation_fresh
        assert not await is_selected_generation_fresh(session, scenes[1], video)
        mixed_entries = []
        for scene in scenes:
            selected = await session.get(SceneGeneration, scene.selected_generation_id)
            output = await session.get(Asset, selected.output_asset_id)
            mixed_entries.append(
                {
                    "scene_id": scene.id,
                    "scene_order": scene.scene_order,
                    "generation_id": selected.id,
                    "asset_id": output.id,
                    "asset_checksum": output.checksum,
                }
            )
        # Even an otherwise matching source identity cannot legitimize a mixed run.
        assert not await is_assembly_source_current(
            session,
            video,
            {
                "video_id": video.id,
                "video_revision": video.revision,
                "scenes": mixed_entries,
            },
        )
        with pytest.raises(AppError) as error:
            await AssemblyService().create(
                session,
                video_id=video_id,
                request=AssemblyRequest(),
                expected_revision=video.revision,
                user_id=user,
                request_id=None,
            )
        assert error.value.code == "SCENE_SELECTION_STALE"
        second = await select_generation(
            scene_ids[1],
            Selection(generation_id=newer.generations[1].id),
            revision=scenes[1].revision,
            user=await session.get(User, user),
            session=session,
        )
        assert second.selected_generation_fresh
        assert await is_selected_generation_fresh(session, scenes[0], video)
        final = await AssemblyService().create(
            session,
            video_id=video_id,
            request=AssemblyRequest(),
            expected_revision=video.revision,
            user_id=user,
            request_id=None,
        )
        assert await is_assembly_source_current(session, video, final.manifest)


@pytest.mark.asyncio
async def test_binding_preserves_execution_inputs_and_synchronizes_frozen_member_hashes(
    session_factory,
    tmp_path,
    monkeypatch,
):
    _, _, _, _, run_id = await selected_chain(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session:
        run = await session.get(DirectorRun, run_id)
        generations = [
            await session.get(SceneGeneration, member["scene_generation_id"])
            for member in run.input_snapshot["members"]
        ]
        original = copy.deepcopy(run.input_snapshot)
        bind_execution_group_freshness(generations, run)
        assert run.input_snapshot == original
        binding = run.input_snapshot["execution_group"]
        for member in run.input_snapshot["members"]:
            generation = await session.get(SceneGeneration, member["scene_generation_id"])
            assert member["input_snapshot"] == generation.input_snapshot
            assert generation.input_snapshot["generation_freshness"]["execution_group"] == binding
            unsigned = {k: v for k, v in generation.input_snapshot.items() if k != "semantic_hash"}
            assert generation.input_snapshot["semantic_hash"] == stable_hash(unsigned)
        unsigned = {k: v for k, v in run.input_snapshot.items() if k != "semantic_hash"}
        assert run.input_snapshot["semantic_hash"] == stable_hash(unsigned)


@pytest.mark.asyncio
async def test_partial_prefix_binding_cannot_certify_full_native_chain(
    session_factory,
    tmp_path,
    monkeypatch,
):
    from apps.api.app.services.director_run_service import (
        create_director_run,
        persist_prepared_director_run,
    )
    from apps.api.app.services.generation_service import GenerationService

    user, video_id, scene_ids, generations = await prepared_members(
        session_factory,
        tmp_path,
        2,
        persist=False,
    )
    async with session_factory() as session, session.begin():
        scenes = [await session.get(Scene, key) for key in scene_ids]
        video = await session.get(Video, video_id)
        await qualify_run(session, generations[:1], scenes[:1], video, user, monkeypatch)
        run = await create_director_run(
            session, generations[:1], scenes[:1], video=video, user_id=user, persist=False
        )
        await GenerationService.persist_prepared(session, generations[0], video)
        await persist_prepared_director_run(session, run)
        run_id, generation_id = run.id, generations[0].id
    await complete_run(session_factory, tmp_path, run_id)
    async with session_factory() as session:
        scene = await session.get(Scene, scene_ids[0])
        video = await session.get(Video, video_id)
        generation = await session.get(SceneGeneration, generation_id)
        assert await is_generation_fresh(session, scene, video, generation)
        assert not await is_selected_generation_fresh(session, scene, video)


@pytest.mark.asyncio
async def test_unrelated_cut_scene_edit_does_not_stale_native_chain(
    session_factory,
    tmp_path,
    monkeypatch,
):
    user, video_id, scene_ids, _, _ = await selected_chain(session_factory, tmp_path, monkeypatch)
    async with session_factory() as session, session.begin():
        extra = Scene(
            video_id=video_id,
            scene_order=2,
            prompt="Unrelated CUT",
            duration_seconds=5,
            spec={"continuity": "CUT"},
        )
        session.add(extra)
        await session.flush()
        extra.prompt = "Edit unrelated CUT"
        video = await session.get(Video, video_id)
        scenes = [await session.get(Scene, key) for key in scene_ids]
        assert all([await is_selected_generation_fresh(session, scene, video) for scene in scenes])
