import asyncio
import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from apps.api.app.api.admin import approve_workflow
from apps.api.app.api.generations import (
    create_regeneration,
    create_variation,
    generate_all,
    generation_capabilities,
    preview_prompt,
)
from apps.api.app.api.scenes import patch_scene
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene, User, Video, WorkflowRecord
from apps.api.app.schemas.api import (
    GenerateAll,
    GenerationRequest,
    PromptPreviewRequest,
    RegenerateRequest,
    ScenePatch,
    VariationRequest,
    WorkflowApproval,
)
from apps.api.app.services.generation_service import GenerationService
from tests.integration.test_generation_preparation import director_workflow_data, seed

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def settings(tmp_path):
    return Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("seed_policy", ["FIXED", "RANDOM"])
async def test_frontend_history_reuse_payload_accepts_current_duration(
    session_factory, tmp_path, seed_policy
):
    """Run the actual TypeScript reuse/editor request builders through the service."""
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(
                seed_policy=seed_policy, seed=42 if seed_policy == "FIXED" else None
            ),
            user_id=user_id,
            request_id=None,
        )
        frozen = copy.deepcopy(parent.input_snapshot)
        assert frozen["frames"] == 124
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        scene.duration_seconds = 8
        scene.revision += 1
        bridge = r"""
const fs = require('node:fs');
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const { loadTypeScript } = require('./frontend/tests/load-typescript.cjs');
const { reuseGenerationSettings } = loadTypeScript('lib/generation/history-settings.ts');
const { createPromptContext, acceptPrompt, acceptedGenerationRequest } =
  loadTypeScript('lib/generation/accepted-prompt.ts');
const before = JSON.stringify(data.parent);
const config = reuseGenerationSettings(data.parent);
const draft = {mode:'AUTO', quality_profile:'STANDARD', seed_policy:'RANDOM', ...config,
  aspect_ratio:config.aspect_ratio ?? data.video.aspect_ratio,
  seed:config.seed_policy === 'FIXED' ? config.seed : null, workflow_id:data.workflow};
const context = createPromptContext(
  data.scene.id,data.scene.revision,data.video.id,data.video.revision,draft);
const accepted = acceptPrompt(context,{
  scene_revision:data.scene.revision,video_revision:data.video.revision
},'Accepted integration prompt');
if (JSON.stringify(data.parent) !== before) throw new Error('history mutated');
process.stdout.write(JSON.stringify({config,request:acceptedGenerationRequest(accepted,context)}));
"""
        data = {
            "parent": {"mode": parent.mode, "input_snapshot": frozen},
            "scene": {"id": scene.id, "revision": scene.revision},
            "video": {
                "id": video.id,
                "revision": video.revision,
                "aspect_ratio": video.aspect_ratio,
            },
            "workflow": parent.workflow_id,
        }
        result = await asyncio.to_thread(
            subprocess.run,
            ["node", "-e", bridge],
            input=json.dumps(data),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
            cwd=REPOSITORY_ROOT,
        )
        payload = json.loads(result.stdout)
        for field in ("width", "height", "frames", "cfg", "fps", "steps", "runtime_profile"):
            assert field not in payload["config"]
            assert field not in payload["request"]
        scene.generation_config = payload["config"]
        request = GenerationRequest.model_validate(payload["request"])
        # Backend must retain strict frame validation; reuse fixes the request.
        with pytest.raises(AppError) as caught:
            await service.create(
                session,
                scene_id=scene_id,
                request=request.model_copy(update={"frames": frozen["frames"]}),
                user_id=user_id,
                request_id=None,
            )
        assert caught.value.code == "GENERATION_INPUT_INVALID"
        generation = await service.create(
            session,
            scene_id=scene_id,
            request=request,
            user_id=user_id,
            request_id=None,
        )
        assert generation.input_snapshot["frames"] == 192
        assert generation.input_snapshot["seed_policy"] == seed_policy
        if seed_policy == "FIXED":
            assert generation.input_snapshot["seed"] == 42
        else:
            assert request.seed is None
        assert parent.input_snapshot == frozen


@pytest.mark.asyncio
async def test_scene_config_batch_and_exact_prompt(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        before = video.revision
        await patch_scene(
            scene_id,
            ScenePatch(
                generation_config={"seed_policy": "FIXED", "seed": 42, "aspect_ratio": "1:1"}
            ),
            revision=1,
            user=user,
            session=session,
        )
        assert scene.revision == 2 and video.revision == before + 1
        dto = await generate_all(
            video.id,
            GenerateAll(),
            request=SimpleNamespace(state=SimpleNamespace()),
            key="config",
            user=user,
            session=session,
            settings=settings(tmp_path),
        )
        frozen = dto.generations[0].input_snapshot
        assert frozen["seed"] == 42
        assert frozen["requested_aspect_ratio"] == "1:1"
        assert frozen["width"] == frozen["resolved_width"] == 640
        prompt = "  exactly accepted\nno additions  "
        generation = await GenerationService(settings(tmp_path)).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(
                execution_prompt=prompt,
                source_scene_revision=scene.revision,
                source_video_revision=video.revision,
            ),
            user_id=user_id,
            request_id=None,
        )
        assert generation.input_snapshot["prompt"] == prompt
        assert generation.input_snapshot["director_execution"]["prompt"] == prompt


@pytest.mark.asyncio
async def test_variation_inherits_frozen_parent_through_actual_api(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        service = GenerationService(settings(tmp_path))
        parent = await service.create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=10),
            user_id=user_id,
            request_id=None,
        )
        parent.status = "COMPLETED"
        frozen = copy.deepcopy(parent.input_snapshot)
        scene = await session.get(Scene, scene_id)
        scene.prompt = "MUTATED CURRENT SCENE"
        scene.duration_seconds = 12
        scene.revision += 1
        child = await create_variation(
            scene_id,
            VariationRequest(parent_generation_id=parent.id),
            request=SimpleNamespace(state=SimpleNamespace()),
            key="variation",
            user=await session.get(User, user_id),
            session=session,
            settings=settings(tmp_path),
        )
        assert child.operation == "VARIATION" and child.parent_generation_id == parent.id
        assert child.input_snapshot["prompt"] == frozen["prompt"]
        assert child.input_snapshot["duration_seconds"] == frozen["duration_seconds"]
        assert child.input_snapshot["runtime_profile"] == frozen["runtime_profile"]
        assert child.input_snapshot["seed"] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unqualified", "steps", "dimensions"])
async def test_legacy_enabled_and_ignored_overrides_rejected(session_factory, tmp_path, change):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        request = GenerationRequest()
        if change == "unqualified":
            from sqlalchemy import select

            workflow = await session.scalar(select(WorkflowRecord))
            workflow.profile = {"poc_verified": False}
        elif change == "steps":
            request = GenerationRequest(steps=8)
        else:
            request = GenerationRequest(width=768, height=768)
        with pytest.raises(AppError):
            await GenerationService(settings(tmp_path)).create(
                session, scene_id=scene_id, request=request, user_id=user_id, request_id=None
            )
        assert video.id


@pytest.mark.asyncio
async def test_caps_and_independent_profile_enablement(session_factory, tmp_path):
    user_id, _ = await seed(session_factory)
    async with session_factory() as session, session.begin():
        standard = await session.scalar(select(WorkflowRecord))
        graph, slots, profile, approved = director_workflow_data(quality="DRAFT", steps=4)
        draft = WorkflowRecord(
            code="DRAFT_TEST",
            mode="t2v",
            quality_profile="DRAFT",
            version="1",
            workflow=graph,
            slots=slots,
            required_slots=list(slots),
            profile=profile,
            workflow_hash=approved.workflow_hash,
            slot_map_hash=approved.slot_map_hash,
            enabled=False,
            created_by=user_id,
        )
        session.add(draft)
        await session.flush()
        user = await session.get(User, user_id)
        await approve_workflow(
            draft.id, WorkflowApproval(enabled=True), admin=user, session=session
        )
        await session.refresh(standard)
        assert standard.enabled and draft.enabled
        caps = await generation_capabilities(user=user, session=session)
        assert caps.available and len(caps.combinations) == 6
        assert {item["quality_profile"] for item in caps.combinations} == {"DRAFT", "STANDARD"}
        standard.profile = {"poc_verified": False}
        draft.enabled = False
        await session.flush()
        caps = await generation_capabilities(user=user, session=session)
        assert not caps.available and caps.combinations == [] and len(caps.disabled) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", [True, False])
async def test_regenerate_seed_policy_and_v1_parent_replay(session_factory, tmp_path, locked):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        parent = await GenerationService(settings(tmp_path)).create(
            session,
            scene_id=scene_id,
            request=GenerationRequest(seed=123) if locked else GenerationRequest(),
            user_id=user_id,
            request_id=None,
        )
        parent.status = "COMPLETED"
        frozen = copy.deepcopy(parent.input_snapshot)
        # Realistic v1 replay retains all historical graph/semantic fields, without new v2 fields.
        parent.input_snapshot = {
            key: value
            for key, value in frozen.items()
            if key
            not in {
                "semantic_hash",
                "profile_hash",
                "base_workflow",
                "requested_aspect_ratio",
                "requested_quality_profile",
                "resolved_width",
                "resolved_height",
                "resolved_duration_seconds",
                "seed_policy",
            }
        }
        parent.input_snapshot = {**parent.input_snapshot, "schema_version": 1}
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        scene.prompt, scene.duration_seconds = "changed", 15
        video.aspect_ratio = "1:1"
        dto = await create_regeneration(
            scene_id,
            RegenerateRequest(
                parent_generation_id=parent.id, seed_policy="FIXED" if locked else "RANDOM"
            ),
            request=SimpleNamespace(state=SimpleNamespace()),
            key="regenerate",
            user=await session.get(User, user_id),
            session=session,
            settings=settings(tmp_path),
        )
        assert dto.operation == "REGENERATE" and dto.id != parent.id
        assert dto.input_snapshot["duration_seconds"] == frozen["duration_seconds"]
        assert dto.input_snapshot["prompt"] == frozen["prompt"]
        assert dto.input_snapshot["width"] == frozen["width"]
        assert (dto.input_snapshot["seed"] == frozen["seed"]) == locked


@pytest.mark.asyncio
async def test_preview_constraints_once_and_stale_acceptance(session_factory, tmp_path):
    user_id, scene_id = await seed(session_factory)
    async with session_factory() as session, session.begin():
        record = await session.scalar(select(WorkflowRecord))
        graph, slots, profile, approved = director_workflow_data()
        record.workflow, record.slots, record.required_slots = graph, slots, list(slots)
        record.profile, record.workflow_hash, record.slot_map_hash = (
            profile,
            approved.workflow_hash,
            approved.slot_map_hash,
        )
    async with session_factory() as session:
        result = await preview_prompt(
            scene_id,
            payload=PromptPreviewRequest(),
            user=await session.get(User, user_id),
            session=session,
            settings=settings(tmp_path),
        )
        assert result.execution_prompt.count("Avoid / Constraints: warped label") == 1
    async with session_factory() as session, session.begin():
        request = GenerationRequest(
            execution_prompt=result.execution_prompt,
            source_scene_revision=result.scene_revision,
            source_video_revision=result.video_revision,
        )
        generation = await GenerationService(settings(tmp_path)).create(
            session, scene_id=scene_id, request=request, user_id=user_id, request_id=None
        )
        assert generation.input_snapshot["prompt"] == result.execution_prompt
        assert generation.input_snapshot["director_execution"]["negative_prompt"] == "warped label"
        scene = await session.get(Scene, scene_id)
        scene.revision += 1
        with pytest.raises(AppError) as error:
            await GenerationService(settings(tmp_path)).create(
                session, scene_id=scene_id, request=request, user_id=user_id, request_id=None
            )
        assert error.value.code == "PROMPT_PREVIEW_STALE"
