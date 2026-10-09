"""Test-only measured evidence at real creation and frozen dispatch boundaries."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from apps.api.app.api.generations import generation_capabilities
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    GenerationAsset,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
)
from apps.api.app.providers.minimax_h3_director.qualification import director_settings_identity
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.workflow_contracts import profile_hash, require_frozen
from tests.integration.test_generation_preparation import seed
from tests.unit.test_director_qualification_corrections import qualified_record, settings_case
from tests.unit.test_refine_face_qualification import settings


async def test_single_frozen_dispatch_rejects_retracted_optional_settings(
    session_factory, tmp_path
):
    user, scene_id = await seed(session_factory)
    record, approved, _ = qualified_record()
    config = settings(True, True)
    config["refine"].update(mode="upscale", megapixels=2.0)
    settings_case(record.profile, config, output_canvas=(1952, 1088))
    async with session_factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        workflow.mode = "t2v"
        workflow.execution_scope = "single_scene"
        workflow.quality_profile = "BASE"
        workflow.workflow = approved.workflow
        workflow.slots = {key: list(value) for key, value in approved.slots.items()}
        workflow.required_slots = []
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.profile = record.profile
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.aspect_ratio = "16:9"
        scene.negative_prompt = ""
        scene.spec = {"continuity": "CUT"}
        await session.flush()
        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        generation = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(
                mode="t2v",
                quality_profile="BASE",
                seed=42,
                refine=config["refine"],
                face_refine=config["face_refine"],
            ),
            user_id=user,
            request_id="synthetic-qualification-correction",
        )
        require_frozen(generation.input_snapshot, workflow)
        profile = deepcopy(workflow.profile)
        profile["execution_evidence"]["director_settings"] = []
        workflow.profile = profile
        with pytest.raises(AppError, match="settings"):
            require_frozen(generation.input_snapshot, workflow)


@pytest.mark.parametrize("operation", ["VARIATION", "REGENERATE"])
@pytest.mark.parametrize("timeline_case", ["inherit", "replace", "empty", "null"])
async def test_standalone_director_derivative_inherits_or_replaces_timeline(
    session_factory, tmp_path, operation, timeline_case
):
    user_id, scene_id = await seed(session_factory)
    record, approved, _ = qualified_record()
    async with session_factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        workflow.mode = "t2v"
        workflow.execution_scope = "single_scene"
        workflow.quality_profile = "BASE"
        workflow.workflow = approved.workflow
        workflow.slots = {key: list(value) for key, value in approved.slots.items()}
        workflow.required_slots = []
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.profile = record.profile
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.aspect_ratio = "16:9"
        scene.negative_prompt = ""
        scene.spec = {"continuity": "CUT"}
        await session.flush()
        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(mode="t2v", quality_profile="BASE", seed=42),
            user_id=user_id,
            request_id="timeline-inheritance-parent",
        )
        parent.status = "COMPLETED"
        await session.flush()
        parent_snapshot = deepcopy(parent.input_snapshot)
        inherited_timeline = parent_snapshot["generation_intent"]["timeline"]
        assert inherited_timeline[0]["asset_indices"] == {}

        request_values = {
            "operation": operation,
            "parent_generation_id": parent.id,
        }
        if timeline_case == "replace":
            request_values["timeline"] = [
                {
                    "scene_id": scene_id,
                    "prompt": "Explicitly replaced timeline prompt",
                    "start_frame": 0,
                    "frame_count": parent_snapshot["frames"],
                    "continuity_from_previous": False,
                }
            ]
        elif timeline_case == "empty":
            request_values["timeline"] = []
        elif timeline_case == "null":
            request_values["timeline"] = None
        derivative = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(**request_values),
            user_id=user_id,
            request_id=f"timeline-inheritance-{operation.lower()}",
        )

        child_timeline = derivative.input_snapshot["generation_intent"]["timeline"]
        if timeline_case == "replace":
            assert child_timeline[0]["prompt"] == "Explicitly replaced timeline prompt"
        elif timeline_case == "inherit":
            assert child_timeline == inherited_timeline
        else:
            assert len(child_timeline) == 1
            assert child_timeline[0]["prompt"] == parent_snapshot["prompt"]
            assert child_timeline[0]["asset_indices"] == {}
        assert parent.input_snapshot == parent_snapshot


async def test_capabilities_and_creation_filter_exact_executed_input(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    record, approved, _ = qualified_record()
    profile = record.profile
    profile["resolution"]["canvases"]["Custom"] = [1728, 960]
    second = deepcopy(profile["execution_evidence"]["combinations"][0])
    second.update(aspect_ratio="Custom", width=1728, height=960)
    second["output"].update(width=1728, height=960)
    profile["execution_evidence"]["combinations"].append(second)
    profile["execution_evidence"]["profile_hash"] = profile_hash(profile)
    config = settings(True, True)
    config["refine"].update(mode="upscale", megapixels=2.0)
    settings_case(profile, config, output_canvas=(1952, 1088))
    async with session_factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        workflow.mode = "t2v"
        workflow.execution_scope = "single_scene"
        workflow.quality_profile = "BASE"
        workflow.workflow = approved.workflow
        workflow.slots = {key: list(value) for key, value in approved.slots.items()}
        workflow.required_slots = []
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.profile = profile
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        video.aspect_ratio = "16:9"
        scene.negative_prompt = ""
        scene.spec = {"continuity": "CUT"}
        await session.flush()
        caps = await generation_capabilities(user=await session.get(User, user_id), session=session)
        assert not caps.disabled
        small = next(c for c in caps.combinations if c["aspect_ratio"] == "16:9")
        large = next(c for c in caps.combinations if c["aspect_ratio"] == "Custom")
        assert small["supports_refine"] and small["supports_face_refine"]
        assert small["director_settings"] == [config]
        assert not large["supports_refine"] and not large["supports_face_refine"]
        assert large["director_settings"] == []
        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(mode="t2v", quality_profile="BASE", seed=42),
            user_id=user_id,
            request_id="synthetic-baseline",
        )
        parent.status = "COMPLETED"
        await session.flush()
        before = list(await session.scalars(select(SceneGeneration.id)))
        parent_snapshot = deepcopy(parent.input_snapshot)
        parent_state = (parent.status, parent.phase, parent.workflow_id, parent.generation_no)
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
        reference_profile = deepcopy(profile)
        for combination in reference_profile["execution_evidence"]["combinations"]:
            combination["mode"] = "r2v"
        for case in reference_profile["execution_evidence"]["director_settings"]:
            case["task"] = "r2v"
            case["settings_hash"] = director_settings_identity(
                case["settings"], base_canvas=case["base_canvas"], task="r2v"
            )
        reference_workflow = WorkflowRecord(
            code="SYNTHETIC_REFERENCE",
            mode="r2v",
            version=workflow.version,
            quality_profile="BASE",
            execution_scope="single_scene",
            workflow=workflow.workflow,
            slots=workflow.slots,
            required_slots=[],
            workflow_hash=workflow.workflow_hash,
            slot_map_hash=workflow.slot_map_hash,
            profile=reference_profile,
            enabled=True,
            created_by=user_id,
        )
        session.add(reference_workflow)
        await session.flush()
        for operation in ("ORIGINAL", "VARIATION", "REGENERATE"):
            request = GenerationRequest(
                mode="r2v",
                workflow_id=reference_workflow.id,
                reference_image_asset_ids=[asset.id],
                quality_profile="BASE",
                seed=42,
                operation=operation,
                parent_generation_id=parent.id if operation != "ORIGINAL" else None,
                aspect_ratio="Custom",
                width=1728,
                height=960,
                timeline=[],
                refine=config["refine"],
                face_refine=config["face_refine"],
            )
            with pytest.raises(AppError, match="settings"):
                await service.create(
                    session,
                    scene_id=scene_id,
                    request=request,
                    user_id=user_id,
                    request_id="synthetic-collision",
                )
            await session.flush()
            assert list(await session.scalars(select(SceneGeneration.id))) == before
            assert parent.input_snapshot == parent_snapshot
            assert (
                parent.status,
                parent.phase,
                parent.workflow_id,
                parent.generation_no,
            ) == parent_state
            assert await session.scalar(select(func.count()).select_from(GenerationAsset)) == 0
            assert not list(
                await session.scalars(
                    select(SceneGeneration.id).where(
                        SceneGeneration.parent_generation_id == parent.id
                    )
                )
            )
    async with session_factory() as session:
        assert list(await session.scalars(select(SceneGeneration.id))) == before
        persisted_parent = await session.get(SceneGeneration, parent.id)
        assert persisted_parent.input_snapshot == parent_snapshot
        assert (
            persisted_parent.status,
            persisted_parent.phase,
            persisted_parent.workflow_id,
            persisted_parent.generation_no,
        ) == parent_state
        assert await session.scalar(select(func.count()).select_from(GenerationAsset)) == 0
