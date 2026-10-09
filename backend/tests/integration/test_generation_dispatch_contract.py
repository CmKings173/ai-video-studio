import copy
import hashlib

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.db.models import Asset, Scene, SceneGeneration, WorkflowRecord
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_contract_edges import reference_fixture
from tests.integration.test_generation_preparation import seed
from tests.integration.test_generation_races import MemoryStore, ProbeOnlyFFmpeg, RejectingAdapter
from workers.dispatcher import Dispatcher


@pytest.mark.asyncio
async def test_dispatch_rejects_newly_unqualified_workflow_without_submit(
    session_factory, tmp_path
):
    user, scene_id = await seed(session_factory)
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    async with session_factory() as session, session.begin():
        generation = await GenerationService(settings).create(
            session, scene_id=scene_id, request=GenerationRequest(), user_id=user, request_id=None
        )
        generation_id = generation.id
        workflow = await session.get(WorkflowRecord, generation.workflow_id)
        workflow.profile = {**workflow.profile, "poc_verified": False}
    adapter = RejectingAdapter()
    dispatcher = Dispatcher(session_factory, adapter, MemoryStore(), ProbeOnlyFFmpeg(), settings)
    assert await dispatcher.run_once()
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "FAILED"
        assert generation.error_code == "WORKFLOW_NOT_QUALIFIED"
    assert adapter.submit_count == 0


@pytest.mark.asyncio
async def test_completion_metadata_is_separate_from_frozen_inputs(session_factory, tmp_path):
    user, scene_id = await seed(session_factory)
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    dispatcher = Dispatcher(session_factory, None, None, None, settings)
    metadata = {
        "width": 480,
        "height": 864,
        "fps": 24.0,
        "frames": 124,
        "duration_seconds": 5.167,
        "has_audio": True,
        "has_video": True,
        "inspection_method": "test-only-observation",
        "checksum": hashlib.sha256(b"test").hexdigest(),
    }
    async with session_factory() as session, session.begin():
        generation = await GenerationService(settings).create(
            session, scene_id=scene_id, request=GenerationRequest(), user_id=user, request_id=None
        )
        generation.status = "COLLECTING"
        generation.claimed_by = dispatcher.owner
        frozen = copy.deepcopy(generation.input_snapshot)
        generation_id = generation.id
        scene = await session.get(Scene, scene_id)
        asset = Asset(
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="test.mp4",
            content_type="video/mp4",
            object_key="out/test",
            status="READY",
            size_bytes=4,
            checksum=metadata["checksum"],
            created_by=user,
            media_metadata=metadata,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
        assert scene.revision == frozen["scene_revision"]
    await dispatcher._finish(generation_id, "COMPLETED", asset_id=asset_id)
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert all(generation.output_metadata[key] == value for key, value in metadata.items())
        assert generation.output_metadata["size_bytes"] == 4
        assert generation.output_metadata["measured"]["width"] == 480
        assert generation.output_metadata["requested"]["canvas"]["width"] == frozen["width"]
        assert generation.input_snapshot == frozen


@pytest.mark.asyncio
async def test_changed_parent_reference_rejected_before_upload_or_submit(session_factory, tmp_path):
    user, scene_id, assets = await reference_fixture(session_factory)
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    async with session_factory() as session, session.begin():
        generation = await GenerationService(settings).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(reference_image_asset_ids=[assets[0]]),
            user_id=user,
            request_id=None,
        )
        generation_id = generation.id
        asset = await session.get(Asset, assets[0])
        asset.checksum = "e" * 64
    adapter = RejectingAdapter()
    dispatcher = Dispatcher(session_factory, adapter, MemoryStore(), ProbeOnlyFFmpeg(), settings)
    assert await dispatcher.run_once()
    async with session_factory() as session:
        generation = await session.get(SceneGeneration, generation_id)
        assert generation.status == "FAILED" and generation.error_code == "ASSET_INTEGRITY_FAILED"
    assert adapter.submit_count == 0
