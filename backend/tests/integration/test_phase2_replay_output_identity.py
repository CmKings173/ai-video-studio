from __future__ import annotations

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from apps.api.app.db.models import (
    Asset,
    GenerationAsset,
    IdempotencyKey,
    Project,
    Scene,
    SceneGeneration,
    Video,
    utcnow,
)
from apps.api.app.schemas.api import GenerationDTO, GenerationRequest
from apps.api.app.services.idempotency import payload_hash
from tests.integration.test_generation_replay_authorization import (
    api_client,
    login,
    seed_replay_world,
)


def _worker_output_id(generation_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{generation_id}"))


def _output_asset(
    *, asset_id: str, project_id: str, owner_id: str, legacy: bool = False
) -> Asset:
    checksum = "d" * 64
    return Asset(
        id=asset_id,
        project_id=project_id,
        resource_owner_id=None if legacy else owner_id,
        ownership_provenance=None if legacy else "VIDEO_PARENT",
        kind="VIDEO",
        role="GENERATED_VIDEO",
        filename=f"{asset_id}.mp4",
        content_type="video/mp4",
        object_key=f"outputs/generated_video/{asset_id}/{checksum}.mp4",
        status="READY",
        size_bytes=1024,
        checksum=checksum,
        created_by=owner_id,
    )


async def _save_output_asset(
    session, *, asset_id: str, project_id: str, owner_id: str, legacy: bool = False
) -> Asset:
    candidate = _output_asset(
        asset_id=asset_id, project_id=project_id, owner_id=owner_id, legacy=legacy
    )
    asset = await session.get(Asset, asset_id)
    if asset is None:
        session.add(candidate)
        return candidate
    for column in Asset.__table__.columns:
        if column.name not in {"id", "created_at", "updated_at"}:
            setattr(asset, column.name, getattr(candidate, column.name))
    return asset


async def _add_generation(
    session, *, video_id: str, scene_id: str, workflow_id: str, owner_id: str,
    generation_no: int = 1,
) -> SceneGeneration:
    generation = SceneGeneration(
        video_id=video_id,
        scene_id=scene_id,
        mode="t2v",
        workflow_id=workflow_id,
        generation_no=generation_no,
        operation="ORIGINAL",
        status="COMPLETED",
        phase="COMPLETED",
        input_snapshot={},
        output_metadata={},
        created_by=owner_id,
    )
    session.add(generation)
    await session.flush()
    return generation


async def _make_pointer_target(session, ids: dict[str, str], kind: str):
    owner_id = ids["editora_user"]
    if kind == "same_owner_sibling":
        target = await _add_generation(
            session,
            video_id=ids["editora_video"],
            scene_id=ids["editora_scene"],
            workflow_id=ids["editora_workflow"],
            owner_id=owner_id,
            generation_no=2,
        )
        project_id = ids["editora_project"]
    elif kind in {"same_owner_other_video", "same_owner_other_project"}:
        if kind == "same_owner_other_project":
            project = Project(name="Second EditorA project", created_by=owner_id)
            session.add(project)
            await session.flush()
            project_id = project.id
        else:
            project_id = ids["editora_project"]
        video = Video(
            project_id=project_id,
            title=f"Second video for {kind}",
            brief="Replay output identity fixture",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="16:9",
            created_by=owner_id,
        )
        session.add(video)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Second scene", duration_seconds=5)
        session.add(scene)
        await session.flush()
        target = await _add_generation(
            session,
            video_id=video.id,
            scene_id=scene.id,
            workflow_id=ids["editora_workflow"],
            owner_id=owner_id,
        )
    elif kind == "different_owner":
        target = await session.get(SceneGeneration, ids["editorb_generation"])
        project_id = ids["editorb_project"]
        owner_id = ids["editorb_user"]
    else:
        raise AssertionError(f"unexpected pointer target: {kind}")

    target_output_id = _worker_output_id(target.id)
    await _save_output_asset(
        session,
        asset_id=target_output_id,
        project_id=project_id,
        owner_id=owner_id,
    )
    target.output_asset_id = target_output_id
    await session.flush()
    return target_output_id


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["editora", "admin"])
@pytest.mark.parametrize(
    ("pointer_source", "expected_status"),
    [
        ("own_generation", 202),
        ("same_owner_sibling", 404),
        ("same_owner_other_video", 404),
        ("same_owner_other_project", 404),
        ("different_owner", 404),
        ("legacy_canonical_identity", 202),
        ("legacy_ambiguous_identity", 404),
        ("linked_reference_image", 404),
    ],
)
async def test_http_replay_requires_independent_output_identity_proof(
    session_factory, tmp_path, actor, pointer_source, expected_status
):
    ids = await seed_replay_world(session_factory)
    generation_id = ids["editora_generation"]
    scene_id = ids["editora_scene"]
    key = f"output-identity-{actor}-{pointer_source}"
    payload = GenerationRequest()
    request_data = payload.model_dump(mode="json", exclude_unset=True)

    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        assert generation is not None
        if pointer_source == "own_generation":
            output_id = _worker_output_id(generation_id)
            await _save_output_asset(
                session,
                asset_id=output_id,
                project_id=ids["editora_project"],
                owner_id=ids["editora_user"],
            )
            generation.output_asset_id = output_id
        elif pointer_source in {
            "same_owner_sibling", "same_owner_other_video", "same_owner_other_project",
            "different_owner",
        }:
            output_id = await _make_pointer_target(session, ids, pointer_source)
            generation.output_asset_id = output_id
        elif pointer_source in {"legacy_canonical_identity", "legacy_ambiguous_identity"}:
            output_id = (
                _worker_output_id(generation_id)
                if pointer_source == "legacy_canonical_identity"
                else str(uuid4())
            )
            await _save_output_asset(
                session,
                asset_id=output_id,
                project_id=ids["editora_project"],
                owner_id=ids["editora_user"],
                legacy=True,
            )
            generation.output_asset_id = output_id
        else:
            output_id = ids["editora_asset"]
            generation.input_snapshot = {"assets": [{"id": output_id}]}
            session.add(
                GenerationAsset(
                    generation_id=generation_id,
                    asset_id=output_id,
                    role="REFERENCE_IMAGE",
                    order_index=0,
                )
            )
            await session.flush()

        if pointer_source == "linked_reference_image":
            # Preserve the original null cached pointer while the canonical row is poisoned.
            cached = GenerationDTO.model_validate(generation).model_dump(mode="json")
            session.add(
                IdempotencyKey(
                    user_id=ids[f"{actor}_user"],
                    operation=f"create-generation:{scene_id}",
                    key=key,
                    request_hash=payload_hash(request_data),
                    response=cached,
                    status_code=202,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )
            await session.flush()
            generation.output_asset_id = output_id
        else:
            await session.flush()
            cached = GenerationDTO.model_validate(generation).model_dump(mode="json")
            session.add(
                IdempotencyKey(
                    user_id=ids[f"{actor}_user"],
                    operation=f"create-generation:{scene_id}",
                    key=key,
                    request_hash=payload_hash(request_data),
                    response=cached,
                    status_code=202,
                    expires_at=utcnow() + timedelta(hours=1),
                )
            )

    async with api_client(session_factory, tmp_path) as client:
        await login(client, f"{actor}@ownership.test")
        response = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers={"Idempotency-Key": key},
            json=request_data,
        )

    assert response.status_code == expected_status, response.text
    if expected_status == 404:
        assert response.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    else:
        assert response.json()["output_asset_id"] == output_id
