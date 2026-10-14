from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import select

from apps.api.app.core.config import Settings
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
    IdempotencyKey,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.providers.minimax_h3_director.collector import artifact_manifest
from apps.api.app.providers.minimax_h3_director.contracts import artifact_member_range
from apps.api.app.schemas.api import GenerationDTO, GenerationRequest
from apps.api.app.services import director_run_service
from apps.api.app.services.director_run_service import (
    persist_prepared_director_run,
    require_frozen_director_run,
)
from apps.api.app.services.executable_weights import collect_executable_weights
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.idempotency import payload_hash
from apps.api.app.services.workflow_contracts import profile_hash, require_contract
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.integration.test_assets_assembly_ownership import PASSWORD
from tests.integration.test_director_dynamic_aggregate import qualify_run
from tests.integration.test_generation_preparation import seed
from tests.integration.test_generation_replay_authorization import (
    api_client,
    login,
)


def _aggregate_h3_entry_and_graph():
    workflow_directory = Path(__file__).resolve().parents[2] / "workflows" / "h3"
    entries = json.loads((workflow_directory / "registry.json").read_text())["workflows"]
    entry = next(item for item in entries if item["code"] == "H3_DIRECTOR_T2V_BASE_AGGREGATE")
    return entry, json.loads((workflow_directory / entry["file"]).read_text())


async def _seed_frozen_replay_run(
    session_factory,
    tmp_path,
    monkeypatch,
    *,
    with_pre_refine,
    run_created_by_admin=False,
):
    user_id, first_scene_id = await seed(session_factory)
    entry, graph = _aggregate_h3_entry_and_graph()
    approved = ApprovedWorkflow(
        "t2v",
        entry["version"],
        graph,
        {key: tuple(value) for key, value in entry["slots"].items()},
        execution_scope="aggregate",
    )
    dependency_versions = {node["class_type"]: "synthetic-test-only" for node in graph.values()}
    weight_hashes = {name: "a" * 64 for name in collect_executable_weights(graph)}
    measured = {
        "width": 864,
        "height": 480,
        "fps": 24.0,
        "duration_seconds": 5.0,
        "frame_count": 120,
        "video_codec": "h264",
        "container": "mp4",
        "has_video": True,
        "has_audio": True,
        "audio_sample_rate": 48000,
        "audio_channels": 2,
        "audio_codec": "aac",
        "checksum": "f" * 64,
        "size_bytes": 1000,
    }
    runtime_values = {
        "model_hash": "c" * 64,
        "lora_hashes": {},
        "comfyui_commit": "d" * 40,
        "studio_revision": "1" * 40,
        "comfyui_version": "0.3.50",
        "custom_node_versions": {},
    }
    profile = deepcopy(entry["profile"])
    profile.update(
        dependency_versions=dependency_versions,
        weight_hashes=weight_hashes,
        execution_spec_hash="2" * 64,
        **runtime_values,
    )
    if with_pre_refine:
        profile["output_artifacts"].append(
            {
                "role": "pre-refine",
                "node_id": profile["output_artifacts"][0]["node_id"],
                "required": True,
                "coverage": "all_members",
                "transport": "studio_native_segments_v1",
            }
        )
    evidence = {
        "executed": True,
        "workflow_hash": approved.workflow_hash,
        "slot_map_hash": approved.slot_map_hash,
        "tested_at": "2026-10-05T00:00:00Z",
        "prompt_id": "synthetic-director-replay-test",
        **runtime_values,
        "director_repository": profile["provider_source"]["repository"],
        "director_commit": profile["provider_source"]["commit"],
        "execution_spec_hash": "2" * 64,
        "gpu_model": "synthetic-test-only",
        "gpu_count": 1,
        "driver_version": "synthetic-test-only",
        "cuda_runtime": "synthetic-test-only",
        "output": measured,
        "dependency_versions": dependency_versions,
        "weight_hashes": weight_hashes,
        "combinations": [
            {
                "mode": "t2v",
                "quality_profile": "BASE",
                "aspect_ratio": "16:9",
                "width": 864,
                "height": 480,
                "steps": profile["steps"],
                "output": deepcopy(measured),
            }
        ],
    }
    profile.update(poc_verified=True, execution_evidence=evidence)
    evidence["profile_hash"] = profile_hash(profile)

    async with session_factory() as session, session.begin():
        workflow = await session.scalar(select(WorkflowRecord))
        assert workflow is not None
        workflow.code = entry["code"]
        workflow.version = entry["version"]
        workflow.mode = "t2v"
        workflow.quality_profile = "BASE"
        workflow.execution_scope = "aggregate"
        workflow.workflow = graph
        workflow.slots = entry["slots"]
        workflow.required_slots = []
        workflow.profile = profile
        workflow.workflow_hash = approved.workflow_hash
        workflow.slot_map_hash = approved.slot_map_hash
        workflow.enabled = True
        video = await session.scalar(select(Video))
        first_scene = await session.get(Scene, first_scene_id)
        assert video is not None and first_scene is not None
        video.aspect_ratio = "16:9"
        first_scene.negative_prompt = ""
        first_scene.spec = {"continuity": "CUT"}
        second_scene = Scene(
            video_id=video.id,
            scene_order=1,
            prompt="Member 1",
            negative_prompt="",
            duration_seconds=5,
            spec={"continuity": "CONTINUOUS"},
        )
        session.add(second_scene)
        await session.flush()
        scenes = [first_scene, second_scene]
        require_contract(workflow, approved)
        user = await session.get(User, user_id)
        assert user is not None
        user.password_hash = hash_password(PASSWORD)
        admin = User(
            email="admin@ownership.test",
            name="Admin",
            password_hash=hash_password(PASSWORD),
            role="ADMIN",
        )
        session.add(admin)
        await session.flush()

        service = GenerationService(
            Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
        )
        generation_actor_id = admin.id if run_created_by_admin else user_id
        generations = [
            await service.create(
                session,
                scene_id=scene.id,
                request=GenerationRequest(mode="t2v", quality_profile="BASE", seed=42),
                user_id=generation_actor_id,
                is_admin=run_created_by_admin,
                request_id="phase2-replay-director-fixture",
                defer_aggregate_qualification=True,
                persist=False,
            )
            for scene in scenes
        ]
        generation_ids = [generation.id for generation in generations]
        await qualify_run(session, generations, scenes, video, user_id, monkeypatch)
        run = await director_run_service.create_director_run(
            session,
            generations,
            scenes,
            video=video,
            user_id=admin.id if run_created_by_admin else user_id,
            is_admin=run_created_by_admin,
            persist=False,
        )
        service.finalize_aggregate_member_configurations(generations, run)
        preview_hash = "b" * 64
        for generation in generations:
            service.bind_batch_preview_configuration_hash(generation, preview_hash, run=run)
        for generation in generations:
            await service.persist_prepared(session, generation, video, director_run=run)
        await persist_prepared_director_run(session, run)
        spec = require_frozen_director_run(run.input_snapshot, workflow)
        assert spec.member_count == len(generation_ids)
        assert run.input_snapshot["output_artifacts"] == workflow.profile["output_artifacts"]
        run.comfy_prompt_id = "director-replay-prompt"
        run.attempt_count = 1

        return {
            "admin_user": admin.id,
            "generation_ids": generation_ids,
            "scene_ids": [scene.id for scene in scenes],
            "user_id": user_id,
            "video_id": video.id,
            "project_id": video.project_id,
            "run_id": run.id,
        }


def _worker_artifacts_for_member(
    run,
    generation_ids,
    member_index,
    *,
    project_id,
    owner_id,
    created_by=None,
):
    frozen = run.input_snapshot
    records_by_node: dict[str, list[dict]] = {}
    for binding in frozen["output_artifacts"]:
        for index in artifact_member_range(binding, frozen["member_count"]):
            records_by_node.setdefault(binding["node_id"], []).append(
                {
                    "role": binding["role"],
                    "member_index": index,
                    "filename": f"{binding['role']}-{index}.mp4",
                    "type": "output",
                }
            )
    history = {
        "outputs": {
            node_id: {"studio_director_artifacts": records}
            for node_id, records in records_by_node.items()
        }
    }
    manifest = artifact_manifest(
        history,
        frozen["runtime_profile"],
        require_final=False,
        member_count=frozen["member_count"],
    )
    artifacts = []
    for manifest_index, artifact in enumerate(manifest):
        if artifact.member_index != member_index:
            continue
        member_generation_id = generation_ids[member_index]
        owner_key = (
            member_generation_id
            if artifact.role == "segment"
            else str(
                uuid5(
                    NAMESPACE_URL,
                    f"director:{run.id}:{artifact.role}:{artifact.member_index}:{artifact.ordinal}",
                )
            )
        )
        asset_id = str(uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{owner_key}"))
        checksum = ("a" if artifact.role == "segment" else "b") * 64
        metadata = {
            "role": artifact.role,
            "member_index": artifact.member_index,
            "ordinal": artifact.ordinal,
            "asset_id": asset_id,
            "provider_locator": artifact.locator,
            "checksum": checksum,
            "size_bytes": 1024,
            "measured": {"has_video": True},
            "execution_hash": frozen["director_execution"]["execution_hash"],
            "prompt_id": run.comfy_prompt_id,
            "attempt_no": run.attempt_count,
        }
        asset = Asset(
            id=asset_id,
            project_id=project_id,
            resource_owner_id=owner_id,
            ownership_provenance="VIDEO_PARENT",
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename=f"{owner_key}.mp4",
            content_type="video/mp4",
            object_key=f"outputs/generated_video/{owner_key}/{checksum}.mp4",
            status="READY",
            size_bytes=1024,
            checksum=checksum,
            created_by=created_by or owner_id,
        )
        artifacts.append((manifest_index, asset, metadata))
    return artifacts


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["editora", "admin"])
@pytest.mark.parametrize(
    ("status", "linked_roles", "metadata_roles"),
    [
        ("COLLECTING", ("segment",), ()),
        ("COLLECTING", ("segment", "pre-refine"), ("segment",)),
        ("COMPLETED", ("segment", "pre-refine"), ("segment",)),
        ("COMPLETED", ("segment", "pre-refine"), ("segment", "pre-refine")),
    ],
)
async def test_http_replay_accepts_worker_linked_director_artifacts_across_lifecycle(
    session_factory, tmp_path, monkeypatch, actor, status, linked_roles, metadata_roles
):
    ids = await _seed_frozen_replay_run(
        session_factory,
        tmp_path,
        monkeypatch,
        with_pre_refine="pre-refine" in linked_roles,
    )
    scene_id = ids["scene_ids"][0]
    actor_user_id = ids["user_id"] if actor == "editora" else ids["admin_user"]
    actor_email = "editor@example.test" if actor == "editora" else "admin@ownership.test"
    key = f"director-artifacts-{actor}-{status}-{len(linked_roles)}-{len(metadata_roles)}"
    payload = GenerationRequest()
    request_data = payload.model_dump(mode="json", exclude_unset=True)

    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, ids["run_id"])
        assert run is not None
        generation_rows = [await session.get(SceneGeneration, key) for key in ids["generation_ids"]]
        members = list(
            (
                await session.scalars(
                    select(DirectorRunMember)
                    .where(DirectorRunMember.director_run_id == run.id)
                    .order_by(DirectorRunMember.member_index)
                )
            ).all()
        )
        assert all(generation is not None for generation in generation_rows)
        assert [member.scene_generation_id for member in members] == ids["generation_ids"]

        artifacts_by_member = [
            _worker_artifacts_for_member(
                run,
                ids["generation_ids"],
                member_index,
                project_id=ids["project_id"],
                owner_id=ids["user_id"],
            )
            for member_index in range(len(ids["generation_ids"]))
        ]
        target_artifacts = {
            metadata["role"]: (manifest_index, asset, metadata)
            for manifest_index, asset, metadata in artifacts_by_member[0]
        }
        assert set(linked_roles).issubset(target_artifacts)
        assert set(metadata_roles).issubset(linked_roles)

        if status == "COLLECTING":
            last_collected_index = max(target_artifacts[role][0] for role in linked_roles)
            persisted_artifacts = [
                item
                for member_artifacts in artifacts_by_member
                for item in member_artifacts
                if item[0] <= last_collected_index
            ]
        else:
            persisted_artifacts = [
                item for member_artifacts in artifacts_by_member for item in member_artifacts
            ]

        for manifest_index, asset, metadata in persisted_artifacts:
            session.add(asset)
            await session.flush()
            generation_for_member = ids["generation_ids"][metadata["member_index"]]
            session.add(
                GenerationAsset(
                    generation_id=generation_for_member,
                    asset_id=asset.id,
                    role=f"DIRECTOR_{metadata['role'].upper().replace('-', '_')}",
                    order_index=manifest_index,
                )
            )

        run.status = run.phase = status
        run.output_manifest = (
            {"artifacts": [metadata for _, _, metadata in persisted_artifacts]}
            if status == "COMPLETED"
            else {}
        )
        for member_index, (generation, member) in enumerate(
            zip(generation_rows, members, strict=True)
        ):
            generation.status = generation.phase = status
            member.status = "COMPLETED" if status == "COMPLETED" else "RUNNING"
            member_artifacts = {
                metadata["role"]: metadata for _, _, metadata in artifacts_by_member[member_index]
            }
            selected = member_artifacts["segment"] if status == "COMPLETED" else None
            generation.output_asset_id = member.output_asset_id = (
                selected["asset_id"] if selected else None
            )
            member.output_metadata = selected or {}
            if member_index == 0 and metadata_roles:
                generation.output_metadata = {
                    "director_run_id": run.id,
                    "artifacts": [target_artifacts[role][2] for role in metadata_roles],
                }
            elif selected:
                generation.output_metadata = {
                    "director_run_id": run.id,
                    "artifacts": [selected],
                }
            else:
                generation.output_metadata = {}

        generation = generation_rows[0]
        cached = GenerationDTO.model_validate(generation).model_dump(mode="json")
        session.add(
            IdempotencyKey(
                user_id=actor_user_id,
                operation=f"create-generation:{scene_id}",
                key=key,
                request_hash=payload_hash(request_data),
                response=cached,
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with api_client(session_factory, tmp_path) as client:
        await login(client, actor_email)
        response = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers={"Idempotency-Key": key},
            json=request_data,
        )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == status
    assert response.json()["output_metadata"].get("artifacts", []) == [
        target_artifacts[role][2] for role in metadata_roles
    ]


async def _persist_completed_director_artifacts(
    session, ids, *, artifact_creator_id, omitted_roles=()
):
    """Persist worker Assets while optionally omitting their manifest/link records."""
    run = await session.get(DirectorRun, ids["run_id"])
    assert run is not None
    generation_rows = [
        await session.get(SceneGeneration, generation_id) for generation_id in ids["generation_ids"]
    ]
    members = list(
        (
            await session.scalars(
                select(DirectorRunMember)
                .where(DirectorRunMember.director_run_id == run.id)
                .order_by(DirectorRunMember.member_index)
            )
        ).all()
    )
    assert all(generation is not None for generation in generation_rows)
    assert [member.scene_generation_id for member in members] == ids["generation_ids"]
    artifacts_by_member = [
        _worker_artifacts_for_member(
            run,
            ids["generation_ids"],
            member_index,
            project_id=ids["project_id"],
            owner_id=ids["user_id"],
            created_by=artifact_creator_id,
        )
        for member_index in range(len(ids["generation_ids"]))
    ]
    all_artifacts = [item for member_artifacts in artifacts_by_member for item in member_artifacts]
    persisted = [item for item in all_artifacts if item[2]["role"] not in omitted_roles]
    for manifest_index, asset, metadata in all_artifacts:
        session.add(asset)
        await session.flush()
        if metadata["role"] not in omitted_roles:
            session.add(
                GenerationAsset(
                    generation_id=ids["generation_ids"][metadata["member_index"]],
                    asset_id=asset.id,
                    role=f"DIRECTOR_{metadata['role'].upper().replace('-', '_')}",
                    order_index=manifest_index,
                )
            )
    run.status = run.phase = "COMPLETED"
    run.output_manifest = {"artifacts": [metadata for _, _, metadata in persisted]}
    for generation, member, member_artifacts in zip(
        generation_rows, members, artifacts_by_member, strict=True
    ):
        by_role = {metadata["role"]: metadata for _, _, metadata in member_artifacts}
        selected = by_role["segment"]
        generation.status = generation.phase = "COMPLETED"
        generation.output_asset_id = member.output_asset_id = selected["asset_id"]
        member.status = "COMPLETED"
        member.output_metadata = selected
        # Keep the generation's own summary aligned with the surviving output;
        # the frozen run profile remains authoritative for required artifacts.
        generation.output_metadata = {
            "director_run_id": run.id,
            "artifacts": [selected],
        }
    generation = generation_rows[0]
    cached = GenerationDTO.model_validate(generation).model_dump(mode="json")
    assets_by_role = {
        (metadata["member_index"], metadata["role"]): asset for _, asset, metadata in all_artifacts
    }
    return cached, assets_by_role


@pytest.mark.asyncio
async def test_admin_created_director_output_is_readable_by_video_owner_and_admin(
    session_factory, tmp_path, monkeypatch
):
    ids = await _seed_frozen_replay_run(
        session_factory,
        tmp_path,
        monkeypatch,
        with_pre_refine=False,
        run_created_by_admin=True,
    )
    key_owner = "admin-created-director-output-owner"
    key_admin = "admin-created-director-output-admin"
    request_data = GenerationRequest().model_dump(mode="json", exclude_unset=True)

    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, ids["run_id"])
        video = await session.get(Video, ids["video_id"])
        generations = [
            await session.get(SceneGeneration, generation_id)
            for generation_id in ids["generation_ids"]
        ]
        assert run is not None and video is not None
        assert run.created_by == ids["admin_user"]
        assert video.created_by == ids["user_id"]
        assert run.video_id == video.id
        assert all(
            generation is not None
            and generation.created_by == ids["admin_user"]
            and generation.video_id == video.id
            for generation in generations
        )
        cached, assets_by_role = await _persist_completed_director_artifacts(
            session, ids, artifact_creator_id=ids["admin_user"]
        )
        asset = assets_by_role[(0, "segment")]
        asset_id = asset.id
        assert asset.created_by == ids["admin_user"]
        assert asset.resource_owner_id == ids["user_id"]
        assert asset.ownership_provenance == "VIDEO_PARENT"
        for actor_user_id, key in (
            (ids["user_id"], key_owner),
            (ids["admin_user"], key_admin),
        ):
            session.add(
                IdempotencyKey(
                    user_id=actor_user_id,
                    operation=f"create-generation:{ids['scene_ids'][0]}",
                    key=key,
                    request_hash=payload_hash(request_data),
                    response=cached,
                    status_code=202,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )

    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editor@example.test")
        owner_asset = await client.get(f"/api/v1/assets/{asset_id}")
        assert owner_asset.status_code == 200, owner_asset.text
        owner_replay = await client.post(
            f"/api/v1/scenes/{ids['scene_ids'][0]}/generations",
            headers={"Idempotency-Key": key_owner},
            json=request_data,
        )
        assert owner_replay.status_code == 202, owner_replay.text

        await login(client, "admin@ownership.test")
        admin_asset = await client.get(f"/api/v1/assets/{asset_id}")
        assert admin_asset.status_code == 200, admin_asset.text
        admin_replay = await client.post(
            f"/api/v1/scenes/{ids['scene_ids'][0]}/generations",
            headers={"Idempotency-Key": key_admin},
            json=request_data,
        )
        assert admin_replay.status_code == 202, admin_replay.text


@pytest.mark.asyncio
async def test_completed_director_replay_rejects_missing_required_frozen_artifact(
    session_factory, tmp_path, monkeypatch
):
    ids = await _seed_frozen_replay_run(
        session_factory, tmp_path, monkeypatch, with_pre_refine=True
    )
    key = "director-replay-required-pre-refine-omitted"
    request_data = GenerationRequest().model_dump(mode="json", exclude_unset=True)

    async with session_factory() as session, session.begin():
        run = await session.get(DirectorRun, ids["run_id"])
        assert run is not None
        required_bindings = [
            binding
            for binding in run.input_snapshot["output_artifacts"]
            if binding["role"] == "pre-refine" and binding["required"] is True
        ]
        assert len(required_bindings) == 1
        cached, assets_by_role = await _persist_completed_director_artifacts(
            session,
            ids,
            artifact_creator_id=ids["user_id"],
            omitted_roles=("pre-refine",),
        )
        assert (0, "segment") in assets_by_role
        assert (0, "pre-refine") in assets_by_role
        pre_refine_asset_id = assets_by_role[(0, "pre-refine")].id
        assert pre_refine_asset_id != assets_by_role[(0, "segment")].id
        persisted_pre_refine_asset_id = await session.scalar(
            select(Asset.id).where(Asset.id == pre_refine_asset_id)
        )
        assert persisted_pre_refine_asset_id == pre_refine_asset_id
        assert all(
            artifact["role"] != "pre-refine" for artifact in run.output_manifest["artifacts"]
        )
        linked_roles = set(
            (
                await session.scalars(
                    select(GenerationAsset.role).where(
                        GenerationAsset.generation_id.in_(ids["generation_ids"])
                    )
                )
            ).all()
        )
        assert "DIRECTOR_SEGMENT" in linked_roles
        assert "DIRECTOR_PRE_REFINE" not in linked_roles
        pre_refine_links = list(
            (
                await session.scalars(
                    select(GenerationAsset.asset_id).where(
                        GenerationAsset.asset_id == pre_refine_asset_id
                    )
                )
            ).all()
        )
        assert pre_refine_links == []
        session.add(
            IdempotencyKey(
                user_id=ids["user_id"],
                operation=f"create-generation:{ids['scene_ids'][0]}",
                key=key,
                request_hash=payload_hash(request_data),
                response=cached,
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editor@example.test")
        response = await client.post(
            f"/api/v1/scenes/{ids['scene_ids'][0]}/generations",
            headers={"Idempotency-Key": key},
            json=request_data,
        )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "GENERATION_NOT_FOUND"
