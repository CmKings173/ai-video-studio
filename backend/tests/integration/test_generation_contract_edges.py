import copy
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Project, Scene, WorkflowRecord
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_contract_api import settings
from tests.integration.test_generation_preparation import director_workflow_data, seed


async def reference_fixture(factory):
    user, scene_id = await seed(factory)
    async with factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        from apps.api.app.db.models import Video

        video = await session.get(Video, scene.video_id)
        graph, slots, profile, approved = director_workflow_data(mode="r2v")
        profile["max_reference_images"] = 1
        from apps.api.app.services.workflow_contracts import profile_hash

        profile["execution_evidence"]["profile_hash"] = profile_hash(profile)
        record = await session.scalar(select(WorkflowRecord))
        record.mode, record.workflow, record.slots = "r2v", graph, slots
        record.required_slots, record.profile = list(slots), profile
        record.workflow_hash, record.slot_map_hash = approved.workflow_hash, approved.slot_map_hash
        assets = []
        for index in range(2):
            asset = Asset(
                project_id=video.project_id,
                kind="IMAGE",
                role="REFERENCE",
                filename="image.png",
                content_type="image/png",
                status="READY",
                size_bytes=4,
                object_key=f"test/{index}",
                checksum=hashlib.sha256(str(index).encode()).hexdigest(),
                width=32,
                height=32,
                created_by=user,
            )
            session.add(asset)
            assets.append(asset)
        await session.flush()
        return user, scene_id, [asset.id for asset in assets]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["duplicate", "scope", "dimensions", "checksum", "capacity"])
async def test_reference_inputs_rejected_before_acceptance(session_factory, tmp_path, change):
    user, scene_id, ids = await reference_fixture(session_factory)
    async with session_factory() as session, session.begin():
        selected = [ids[0]]
        asset = await session.get(Asset, ids[0])
        if change == "duplicate":
            selected = [ids[0], ids[0]]
        elif change == "capacity":
            selected = ids
        elif change == "scope":
            project = Project(name="Other", created_by=user)
            session.add(project)
            await session.flush()
            asset.project_id = project.id
        elif change == "dimensions":
            asset.width = None
        else:
            asset.checksum = "invalid"
        with pytest.raises(AppError):
            await GenerationService(settings(tmp_path)).create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(reference_image_asset_ids=selected),
                user_id=user,
                request_id=None,
            )


@pytest.mark.asyncio
async def test_reference_prompt_and_variation_assets_remain_frozen(session_factory, tmp_path):
    user, scene_id, ids = await reference_fixture(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(reference_image_asset_ids=[ids[0]]),
            user_id=user,
            request_id=None,
        )
        parent.status = "COMPLETED"
        original = copy.deepcopy(parent.input_snapshot)
        asset = await session.get(Asset, ids[0])
        asset.media_metadata = {"new_observation": "must not replace frozen parent metadata"}
        scene = await session.get(Scene, scene_id)
        from apps.api.app.db.models import Video

        video = await session.get(Video, scene.video_id)
        accepted = "  <Picture 1> exactly one accepted reference  "
        child = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(
                operation="VARIATION",
                parent_generation_id=parent.id,
                execution_prompt=accepted,
                source_scene_revision=scene.revision,
                source_video_revision=video.revision,
            ),
            user_id=user,
            request_id=None,
        )
        assert child.input_snapshot["prompt"] == accepted
        assert child.input_snapshot["assets"] == original["assets"]
        assert parent.input_snapshot == original


@pytest.mark.asyncio
async def test_parent_raw_canvas_pair_error_is_structured(session_factory, tmp_path):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session, scene_id=scene_id, request=GenerationRequest(), user_id=user, request_id=None
        )
        parent.status = "COMPLETED"
        with pytest.raises(AppError):
            await service.create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(
                    operation="VARIATION", parent_generation_id=parent.id, height=864
                ),
                user_id=user,
                request_id=None,
            )


@pytest.mark.asyncio
async def test_old_snapshot_regeneration_uses_new_seed_when_lock_unknown(session_factory, tmp_path):
    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session, scene_id=scene_id, request=GenerationRequest(), user_id=user, request_id=None
        )
        parent.status = "COMPLETED"
        parent.input_snapshot = {
            key: value
            for key, value in parent.input_snapshot.items()
            if key not in {"seed_policy", "semantic_hash"}
        }
        parent.input_snapshot = {**parent.input_snapshot, "schema_version": 1}
        child = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(operation="REGENERATE", parent_generation_id=parent.id),
            user_id=user,
            request_id=None,
        )
        assert child.input_snapshot["seed"] != parent.input_snapshot["seed"]


@pytest.mark.asyncio
async def test_pre_migration_batch_key_replays_only_original_inputs(session_factory, tmp_path):
    from apps.api.app.api.generations import generate_all
    from apps.api.app.db.models import IdempotencyKey, User, Video
    from apps.api.app.schemas.api import GenerateAll
    from apps.api.app.services.idempotency import payload_hash

    user, scene_id = await seed(session_factory)
    payload = GenerateAll()
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        dto = await generate_all(
            video.id,
            payload,
            request=SimpleNamespace(state=SimpleNamespace()),
            key="historical",
            user=await session.get(User, user),
            session=session,
            settings=settings(tmp_path),
        )
        key = await session.scalar(select(IdempotencyKey))
        saved = copy.deepcopy(key.response)
        saved["_batch"]["inputs"]["scenes"][0]["generation_config"] = None
        legacy = {
            "settings": {
                "workflow_id": None,
                "mode": None,
                "operation": "ORIGINAL",
                "parent_generation_id": None,
                "first_frame_asset_id": None,
                "last_frame_asset_id": None,
                "reference_image_asset_ids": [],
                "reference_audio_asset_ids": [],
                "reference_video_asset_ids": [],
                "execution_prompt": None,
                "source_scene_revision": None,
                "source_video_revision": None,
                "seed": None,
                "width": None,
                "height": None,
                "steps": 8,
            },
            "scene_ids": None,
            "expected_video_revision": None,
            "expected_scene_revisions": None,
        }
        key.response = saved
        key.request_hash = payload_hash({"request": legacy, "inputs": saved["_batch"]["inputs"]})
        replay = await generate_all(
            video.id,
            payload,
            request=SimpleNamespace(state=SimpleNamespace()),
            key="historical",
            user=await session.get(User, user),
            session=session,
            settings=settings(tmp_path),
        )
        assert replay == dto
        with pytest.raises(AppError):
            await generate_all(
                video.id,
                GenerateAll(settings={"quality_profile": "HIGH"}),
                request=SimpleNamespace(state=SimpleNamespace()),
                key="historical",
                user=await session.get(User, user),
                session=session,
                settings=settings(tmp_path),
            )


@pytest.mark.asyncio
async def test_reference_clip_requires_stream_span_not_container_span(session_factory, tmp_path):
    user, scene_id, ids = await reference_fixture(session_factory)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        from apps.api.app.db.models import Video

        video = await session.get(Video, scene.video_id)
        asset = await session.get(Asset, ids[0])
        asset.kind, asset.content_type, asset.duration_seconds = "AUDIO", "audio/mp4", 15
        asset.media_metadata = {"codec": "aac", "inspection_method": "ffprobe_declarations"}
        request = GenerationRequest(reference_audio_asset_ids=[asset.id])
        service = GenerationService(settings(tmp_path))
        with pytest.raises(AppError, match="stream duration"):
            await service._load_assets(session, request, video)
        asset.media_metadata = {**asset.media_metadata, "audio_duration_seconds": 3}
        _, durations, _ = await service._load_assets(session, request, video)
        assert durations == [3]


@pytest.mark.asyncio
async def test_scene_creation_keeps_only_explicit_generation_settings(session_factory):
    from apps.api.app.api.scenes import create_scene
    from apps.api.app.db.models import User, Video
    from apps.api.app.schemas.api import SceneCreate

    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.kind = "LONG_VIDEO"
        dto = await create_scene(
            video.id,
            SceneCreate(prompt="next scene", duration_seconds=5),
            revision=video.revision,
            user=await session.get(User, user),
            session=session,
        )
        assert dto.generation_config == {}


def test_native_graph_cannot_retain_unrequested_frame_conditioning():
    from apps.api.app.services.workflow_router import require_director_execution

    graph = {
        "h3": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"first_frame": ["image", 0]}},
        "image": {"class_type": "LoadImage", "inputs": {"image": "unrequested.png"}},
    }
    with pytest.raises(AppError) as caught:
        require_director_execution(SimpleNamespace(workflow=graph, profile={}))
    assert caught.value.code == "WORKFLOW_RETIRED"


@pytest.mark.asyncio
async def test_parent_operation_preserves_active_project_guard(session_factory, tmp_path):
    from apps.api.app.db.models import Video

    user, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session, scene_id=scene_id, request=GenerationRequest(), user_id=user, request_id=None
        )
        parent.status = "COMPLETED"
        video = await session.get(Video, parent.video_id)
        project = await session.get(Project, video.project_id)
        project.archived = True
        with pytest.raises(AppError) as error:
            await service.create(
                session,
                scene_id=scene_id,
                request=GenerationRequest(operation="REGENERATE", parent_generation_id=parent.id),
                user_id=user,
                request_id=None,
            )
        assert error.value.code == "PROJECT_NOT_ACTIVE"
