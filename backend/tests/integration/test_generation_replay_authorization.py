from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest
from sqlalchemy import func, select

from apps.api.app.api.assets import store
from apps.api.app.core.config import Settings
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
    GenerationAttempt,
    IdempotencyKey,
    Product,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    utcnow,
)
from apps.api.app.db.session import get_session
from apps.api.app.main import app
from apps.api.app.schemas.api import GenerationDTO, GenerationRequest
from apps.api.app.services.idempotency import payload_hash
from tests.integration.test_assets_assembly_ownership import (
    JWT_SECRET,
    PASSWORD,
    RecordingAssetStore,
    seed_world,
)
from tests.integration.test_generation_contract_edges import reference_fixture
from tests.integration.test_generation_preparation import seed as seed_generation_scene


@asynccontextmanager
async def api_client(session_factory, tmp_path):
    async def session_override():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    settings = Settings(
        _env_file=None,
        workspace_root=tmp_path,
        jwt_secret=JWT_SECRET,
        jwt_issuer="ai-video-studio-tests",
        jwt_audience="ai-video-studio-api-tests",
        min_free_disk_bytes=0,
    )
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[store] = lambda: RecordingAssetStore()
    from apps.api.app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def login(client: httpx.AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def _non_null_output_metadata(value) -> dict:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return {key: item for key, item in value.items() if item is not None}


async def seed_replay_world(session_factory) -> dict[str, str]:
    """Give completed fixture generations worker-shaped canonical output Assets."""
    ids = await seed_world(session_factory)
    async with session_factory() as session, session.begin():
        for generation_id in (ids["editora_generation"], ids["editorb_generation"]):
            generation = await session.get(SceneGeneration, generation_id)
            assert generation is not None
            video = await session.get(Video, generation.video_id)
            assert video is not None
            output_id = str(
                uuid5(NAMESPACE_URL, f"ai-video-studio:GENERATED_VIDEO:{generation.id}")
            )
            checksum = "e" * 64
            session.add(
                Asset(
                    id=output_id,
                    project_id=video.project_id,
                    resource_owner_id=video.created_by,
                    ownership_provenance="VIDEO_PARENT",
                    kind="VIDEO",
                    role="GENERATED_VIDEO",
                    filename=f"{generation.id}.mp4",
                    content_type="video/mp4",
                    object_key=f"outputs/generated_video/{generation.id}/{checksum}.mp4",
                    status="READY",
                    size_bytes=1024,
                    checksum=checksum,
                    created_by=generation.created_by,
                )
            )
            generation.output_asset_id = output_id
    return ids


async def row_counts(session_factory) -> tuple[int, int, int, int]:
    async with session_factory() as session:
        counts = []
        for model in (SceneGeneration, GenerationAttempt, DirectorRun, DirectorRunMember):
            counts.append(await session.scalar(select(func.count()).select_from(model)) or 0)
        return tuple(counts)


async def replay_database_state(session_factory, key: str) -> tuple:
    async with session_factory() as session:
        record = await session.scalar(select(IdempotencyKey).where(IdempotencyKey.key == key))
        assert record is not None
        response = deepcopy(record.response)
        generation_ids = []
        if isinstance(response.get("generations"), list):
            generation_ids = [entry["id"] for entry in response["generations"]]
        elif isinstance(response.get("id"), str):
            generation_ids = [response["id"]]
        generations = list(
            (
                await session.scalars(
                    select(SceneGeneration).where(SceneGeneration.id.in_(generation_ids))
                )
            ).all()
        )
        video_ids = {generation.video_id for generation in generations}
        videos = list(
            (
                await session.scalars(select(Video).where(Video.id.in_(video_ids)))
            ).all()
        )
        scenes = list(
            (
                await session.scalars(
                    select(Scene).where(Scene.video_id.in_(video_ids))
                )
            ).all()
        )
        return (
            record.request_hash,
            record.status_code,
            record.expires_at,
            response,
            tuple(
                sorted(
                    (
                        generation.id,
                        generation.status,
                        generation.phase,
                        generation.revision,
                        generation.progress_current,
                        generation.progress_total,
                        generation.output_asset_id,
                        deepcopy(generation.input_snapshot),
                        deepcopy(generation.output_metadata),
                    )
                    for generation in generations
                )
            ),
            tuple(sorted((video.id, video.status, video.revision) for video in videos)),
            tuple(
                sorted(
                    (scene.id, scene.selected_generation_id, scene.revision)
                    for scene in scenes
                )
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached_kind", "expected_status"),
    [
        ("foreign", 404),
        ("wrong_scene", 404),
        ("wrong_parent", 404),
        ("wrong_workflow", 404),
        ("foreign_snapshot", 404),
        ("foreign_output_metadata", 404),
        ("mutable_state", 202),
        ("missing", 409),
        ("valid", 202),
        ("advanced", 202),
    ],
)
async def test_generation_replay_reauthorizes_cached_row_and_preserves_valid_replay(
    session_factory, tmp_path, cached_kind, expected_status
):
    ids = await seed_replay_world(session_factory)
    payload = GenerationRequest()
    request_data = payload.model_dump(mode="json", exclude_unset=True)
    key = f"replay-{cached_kind}"
    async with session_factory() as session, session.begin():
        generation = await session.get(
            SceneGeneration,
            ids["editorb_generation"] if cached_kind == "foreign" else ids["editora_generation"],
        )
        assert generation is not None
        response = GenerationDTO.model_validate(generation).model_dump(mode="json")
        if cached_kind == "wrong_scene":
            scene = Scene(
                video_id=ids["editora_video"],
                scene_order=1,
                prompt="Same owner, different scene",
                duration_seconds=5,
            )
            session.add(scene)
            await session.flush()
            other_generation = SceneGeneration(
                video_id=ids["editora_video"],
                scene_id=scene.id,
                mode="t2v",
                workflow_id=ids["editora_workflow"],
                generation_no=1,
                operation="ORIGINAL",
                status="COMPLETED",
                phase="COMPLETED",
                input_snapshot={},
                created_by=ids["editora_user"],
            )
            session.add(other_generation)
            await session.flush()
            response = GenerationDTO.model_validate(other_generation).model_dump(mode="json")
        elif cached_kind == "wrong_parent":
            response["parent_generation_id"] = ids["editorb_generation"]
        elif cached_kind == "wrong_workflow":
            response["workflow_id"] = ids["editorb_workflow"]
        elif cached_kind == "foreign_snapshot":
            response["input_snapshot"] = {
                "assets": [{"id": ids["editorb_asset"], "object_key": "private/editor-b/asset"}]
            }
        elif cached_kind == "foreign_output_metadata":
            response["output_metadata"] = {
                "artifacts": [
                    {"asset_id": ids["editorb_asset"], "object_key": "private/editor-b/output"}
                ]
            }
        elif cached_kind == "mutable_state":
            response["status"] = "FAILED"
            response["error_message"] = "stale cached state"
            response["output_metadata"] = {"cached_state": "stale"}
        elif cached_kind == "missing":
            response["id"] = "00000000-0000-0000-0000-000000000001"
        session.add(
            IdempotencyKey(
                user_id=ids["editora_user"],
                operation=f"create-generation:{ids['editora_scene']}",
                key=key,
                request_hash=payload_hash(request_data),
                response=response,
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        if cached_kind == "advanced":
            generation.status = "RUNNING"
            generation.phase = "RUNNING"
            generation.progress_current = 3
            generation.progress_total = 10
            generation.revision += 1
            generation.input_snapshot = {"canonical_marker": "live generation state"}
            generation.output_metadata = {"canonical_marker": "live generation state"}

    before = await row_counts(session_factory)
    before_state = await replay_database_state(session_factory, key)
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editora@ownership.test")
        result = await client.post(
            f"/api/v1/scenes/{ids['editora_scene']}/generations",
            headers={"Idempotency-Key": key},
            json=request_data,
        )
    assert result.status_code == expected_status, result.text
    if cached_kind == "valid":
        assert result.json() == response
    if cached_kind == "mutable_state":
        assert result.json()["status"] == "COMPLETED"
        assert result.json()["error_message"] is None
        assert _non_null_output_metadata(result.json()["output_metadata"]) == {}
    if cached_kind == "advanced":
        assert result.json()["status"] == "RUNNING"
        assert result.json()["progress_current"] == 3
        assert result.json()["progress_total"] == 10
        assert result.json()["input_snapshot"] == {"canonical_marker": "live generation state"}
        assert _non_null_output_metadata(result.json()["output_metadata"]) == {
            "canonical_marker": "live generation state"
        }
    assert await row_counts(session_factory) == before
    assert await replay_database_state(session_factory, key) == before_state

@pytest.mark.parametrize("location", ["snapshot", "scene", "source_group"])
@pytest.mark.parametrize("field", ["first_frame_asset_id", "reference_image_asset_ids"])
async def test_admin_replay_denies_poison_in_typed_saved_config_paths(
    session_factory, tmp_path, location, field
):
    ids = await seed_replay_world(session_factory)
    key = f"config-{location}-{field}"
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, ids["editora_generation"])
        config = {field: ([ids["editorb_asset"]] if field.endswith("ids")
                          else ids["editorb_asset"])}
        if location == "snapshot":
            snapshot = {"generation_config": config}
        elif location == "scene":
            snapshot = {"generation_freshness": {"inputs": {
                "scene": {"generation_config": config},
            }}}
        else:
            snapshot = {"generation_freshness": {"inputs": {
                "source_group": [{"generation_config": config}],
            }}}
        # Canonical corruption must be rejected too, rather than simply dropping cache fields.
        generation.input_snapshot = snapshot
        response = GenerationDTO.model_validate(generation).model_dump(mode="json")
        session.add(IdempotencyKey(
            user_id=ids["admin_user"], operation=f"create-generation:{ids['editora_scene']}",
            key=key, request_hash=payload_hash({}), response=response, status_code=202,
            expires_at=utcnow() + timedelta(hours=1),
        ))
    counts = await row_counts(session_factory)
    state = await replay_database_state(session_factory, key)
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "admin@ownership.test")
        result = await client.post(f"/api/v1/scenes/{ids['editora_scene']}/generations",
                                   headers={"Idempotency-Key": key}, json={})
    assert result.status_code == 404, result.text
    assert result.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    assert await row_counts(session_factory) == counts
    assert await replay_database_state(session_factory, key) == state


@pytest.mark.asyncio
async def test_replay_allows_valid_saved_config_asset_omitted_by_request_override(
    session_factory, tmp_path
):
    user_id, scene_id = await seed_generation_scene(session_factory)
    key = "saved-config-reference-override"
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        user.password_hash = hash_password(PASSWORD)
        scene = await session.get(Scene, scene_id)
        video = await session.get(Video, scene.video_id)
        config_asset = Asset(
            project_id=video.project_id,
            kind="IMAGE",
            role="REFERENCE",
            filename="saved-reference.png",
            content_type="image/png",
            status="READY",
            size_bytes=4,
            object_key="test/saved-reference.png",
            checksum="saved-reference-checksum",
            width=32,
            height=32,
            created_by=user_id,
        )
        session.add(config_asset)
        await session.flush()
        config_asset_id = config_asset.id
        scene.generation_config = {"reference_image_asset_ids": [config_asset_id]}

    payload = {"reference_image_asset_ids": []}
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editor@example.test")
        headers = {"Idempotency-Key": key}
        original = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers=headers,
            json=payload,
        )
        assert original.status_code == 202, original.text
        generation_id = original.json()["id"]
        async with session_factory() as session:
            generation = await session.get(SceneGeneration, generation_id)
            assert generation.input_snapshot["assets"] == []
            assert generation.input_snapshot["generation_config"][
                "reference_image_asset_ids"
            ] == [config_asset_id]
            assert await session.scalar(
                select(func.count()).select_from(GenerationAsset).where(
                    GenerationAsset.generation_id == generation_id
                )
            ) == 0
        replay = await client.post(
            f"/api/v1/scenes/{scene_id}/generations",
            headers=headers,
            json=payload,
        )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == generation_id
    assert replay.json()["input_snapshot"]["generation_config"][
        "reference_image_asset_ids"
    ] == [config_asset_id]


@pytest.mark.asyncio
async def test_replay_rejects_input_asset_as_canonical_output_pointer(session_factory, tmp_path):
    user_id, scene_id, asset_ids = await reference_fixture(session_factory)
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        user.password_hash = hash_password(PASSWORD)

    key = "input-asset-as-canonical-output"
    payload = {"reference_image_asset_ids": [asset_ids[0]]}
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editor@example.test")
        headers = {"Idempotency-Key": key}
        original = await client.post(
            f"/api/v1/scenes/{scene_id}/generations", headers=headers, json=payload
        )
        assert original.status_code == 202, original.text
        generation_id = original.json()["id"]
        async with session_factory() as session, session.begin():
            generation = await session.get(SceneGeneration, generation_id)
            assert generation is not None
            link = await session.scalar(
                select(GenerationAsset).where(
                    GenerationAsset.generation_id == generation_id,
                    GenerationAsset.asset_id == asset_ids[0],
                    GenerationAsset.role == "REFERENCE_IMAGE",
                )
            )
            assert link is not None
            generation.output_asset_id = asset_ids[0]

        counts = await row_counts(session_factory)
        state = await replay_database_state(session_factory, key)
        replay = await client.post(
            f"/api/v1/scenes/{scene_id}/generations", headers=headers, json=payload
        )

    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    assert await row_counts(session_factory) == counts
    assert await replay_database_state(session_factory, key) == state


@pytest.mark.parametrize("actor", ["editora", "admin"])
@pytest.mark.parametrize("path", [
    "assets", "generation_intent.assets", "director_execution.assets",
    "lineage", "prompt_provenance", "generation_intent.prompt_provenance",
    "director_execution.prompt_provenance", "artifacts",
])
async def test_replay_structured_reference_poison_denied_for_editor_and_admin(
    session_factory, tmp_path, actor, path
):
    ids = await seed_replay_world(session_factory)
    key = f"structured-{actor}-{path}"
    request_data = {}
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, ids["editora_generation"])
        response = GenerationDTO.model_validate(generation).model_dump(mode="json")
        snapshot = {}
        if path == "artifacts":
            response["output_metadata"] = {"artifacts": [{"asset_id": ids["editorb_asset"]}]}
        else:
            container = snapshot
            parts = path.split(".")
            if len(parts) == 2:
                container = snapshot[parts[0]] = {}
            if parts[-1] == "assets":
                container["assets"] = [{"id": ids["editorb_asset"]}]
            elif path == "lineage":
                container["lineage"] = {"parent_generation_id": ids["editorb_generation"]}
            else:
                container["prompt_provenance"] = {"inherited_from": ids["editorb_generation"]}
            response["input_snapshot"] = snapshot
        session.add(IdempotencyKey(
            user_id=ids[f"{actor}_user"],
            operation=f"create-generation:{ids['editora_scene']}", key=key,
            request_hash=payload_hash(request_data), response=response, status_code=202,
            expires_at=utcnow() + timedelta(hours=1),
        ))
    counts = await row_counts(session_factory)
    state = await replay_database_state(session_factory, key)
    async with api_client(session_factory, tmp_path) as client:
        await login(client, f"{actor}@ownership.test")
        result = await client.post(f"/api/v1/scenes/{ids['editora_scene']}/generations",
                                   headers={"Idempotency-Key": key}, json=request_data)
    assert result.status_code == 404, result.text
    assert result.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    assert await row_counts(session_factory) == counts
    assert await replay_database_state(session_factory, key) == state


@pytest.mark.parametrize("poison", [
    "foreign_linked", "same_owner_unlinked", "output_as_input", "input_as_artifact",
    "input_as_output_pointer",
    "wrong_project", "wrong_product", "unscoped",
])
async def test_admin_replay_requires_business_owner_and_canonical_asset_link(
    session_factory, tmp_path, poison
):
    ids = await seed_replay_world(session_factory)
    key = f"admin-link-{poison}"
    async with session_factory() as session, session.begin():
        generation = await session.get(SceneGeneration, ids["editora_generation"])
        if poison == "foreign_linked":
            asset_id = ids["editorb_asset"]
            session.add(GenerationAsset(generation_id=generation.id, asset_id=asset_id,
                                        role="REFERENCE_IMAGE", order_index=0))
        elif poison == "output_as_input":
            output = Asset(
                project_id=ids["editora_project"],
                kind="VIDEO",
                role="GENERATED_VIDEO",
                filename="generated-output.mp4",
                content_type="video/mp4",
                object_key="test/generated-output.mp4",
                status="READY",
                size_bytes=12,
                checksum="a" * 64,
                created_by=ids["admin_user"],
                resource_owner_id=ids["editora_user"],
                ownership_provenance="VIDEO_PARENT",
            )
            session.add(output)
            await session.flush()
            generation.output_asset_id = output.id
            asset_id = output.id
        else:
            project_id = ids["editora_project"]
            product_id = None
            if poison == "wrong_project":
                project = Project(name="Other owned project", created_by=ids["editora_user"])
                session.add(project)
                await session.flush()
                project_id = project.id
            elif poison == "wrong_product":
                product = Product(name="Other owned product", created_by=ids["editora_user"])
                session.add(product)
                await session.flush()
                project_id, product_id = None, product.id
            elif poison == "unscoped":
                project_id = None
            asset = Asset(kind="IMAGE", filename="unlinked.png", content_type="image/png",
                          object_key="same-owner/unlinked", status="READY", size_bytes=1,
                          project_id=project_id, product_id=product_id,
                          created_by=ids["editora_user"])
            session.add(asset)
            await session.flush()
            asset_id = asset.id
            if poison in {"input_as_artifact", "input_as_output_pointer",
                          "wrong_project", "wrong_product", "unscoped"}:
                session.add(GenerationAsset(generation_id=generation.id, asset_id=asset_id,
                                            role="REFERENCE_IMAGE", order_index=0))
        if poison not in {"input_as_artifact", "input_as_output_pointer"}:
            generation.input_snapshot = {"assets": [{"id": asset_id}]}
        response = GenerationDTO.model_validate(generation).model_dump(mode="json")
        if poison == "input_as_artifact":
            response["output_metadata"] = {"artifacts": [{"asset_id": asset_id}]}
        elif poison == "input_as_output_pointer":
            response["output_asset_id"] = asset_id
        session.add(IdempotencyKey(
            user_id=ids["admin_user"], operation=f"create-generation:{ids['editora_scene']}",
            key=key, request_hash=payload_hash({}), response=response, status_code=202,
            expires_at=utcnow() + timedelta(hours=1),
        ))
    counts = await row_counts(session_factory)
    state = await replay_database_state(session_factory, key)
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "admin@ownership.test")
        result = await client.post(f"/api/v1/scenes/{ids['editora_scene']}/generations",
                                   headers={"Idempotency-Key": key}, json={})
    assert result.status_code == 404, result.text
    assert result.json()["error"]["code"] == "GENERATION_NOT_FOUND"
    assert await row_counts(session_factory) == counts
    assert await replay_database_state(session_factory, key) == state


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "operation", "idempotency_operation"),
    [
        ("variations", "VARIATION", "create-variation"),
        ("regenerations", "REGENERATE", "create-regeneration"),
    ],
)
async def test_derivative_replay_rejects_cached_foreign_parent(
    session_factory, tmp_path, route, operation, idempotency_operation
):
    ids = await seed_replay_world(session_factory)
    key = f"{route}-foreign-parent"
    payload = {"parent_generation_id": ids["editora_generation"]}
    request_data = GenerationRequest.model_validate(
        {**payload, "operation": operation}
    ).model_dump(mode="json", exclude_unset=True)
    async with session_factory() as session, session.begin():
        parent = await session.get(SceneGeneration, ids["editora_generation"])
        assert parent is not None
        derivative = SceneGeneration(
            video_id=ids["editora_video"],
            scene_id=ids["editora_scene"],
            mode=parent.mode,
            workflow_id=parent.workflow_id,
            generation_no=2,
            operation=operation,
            parent_generation_id=parent.id,
            status="CREATED",
            phase="PENDING",
            input_snapshot={"lineage": {"parent_generation_id": parent.id}},
            created_by=ids["editora_user"],
        )
        session.add(derivative)
        await session.flush()
        cached = GenerationDTO.model_validate(derivative).model_dump(mode="json")
        cached["parent_generation_id"] = ids["editorb_generation"]
        session.add(
            IdempotencyKey(
                user_id=ids["editora_user"],
                operation=f"{idempotency_operation}:{ids['editora_scene']}",
                key=key,
                request_hash=payload_hash(request_data),
                response=cached,
                status_code=202,
                expires_at=utcnow() + timedelta(hours=1),
            )
        )

    before = await row_counts(session_factory)
    before_state = await replay_database_state(session_factory, key)
    async with api_client(session_factory, tmp_path) as client:
        await login(client, "editora@ownership.test")
        result = await client.post(
            f"/api/v1/scenes/{ids['editora_scene']}/{route}",
            headers={"Idempotency-Key": key},
            json=payload,
        )
    assert result.status_code == 404, result.text
    assert await row_counts(session_factory) == before
    assert await replay_database_state(session_factory, key) == before_state


async def seed_foreign_video_graph(
    session_factory, workflow_id: str, *, existing_owner_id: str | None = None
):
    async with session_factory() as session, session.begin():
        user = (
            await session.get(User, existing_owner_id)
            if existing_owner_id is not None
            else None
        )
        if user is None:
            user = User(
                email="foreign-batch-owner@example.test",
                name="Foreign owner",
                password_hash=hash_password(PASSWORD),
                role="EDITOR",
            )
            session.add(user)
            await session.flush()
        project = Project(name="Foreign project", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Foreign video",
            brief="Foreign video brief",
            kind="QUICK_CLIP",
            target_duration=5,
            aspect_ratio="16:9",
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="Foreign scene", duration_seconds=5)
        session.add(scene)
        await session.flush()
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode="t2v",
            workflow_id=workflow_id,
            generation_no=1,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={},
            created_by=user.id,
        )
        session.add(generation)
        await session.flush()
        run = DirectorRun(
            video_id=video.id,
            workflow_id=workflow_id,
            task="t2v",
            status="COMPLETED",
            input_snapshot={},
            created_by=user.id,
        )
        session.add(run)
        await session.flush()
        return user.id, video.id, scene.id, generation.id, run.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cached_kind", "expected_status"),
    [
        ("foreign_generation", 404),
        ("same_owner_other_video", 404),
        ("foreign_parent", 404),
        ("wrong_workflow", 404),
        ("foreign_snapshot", 404),
        ("foreign_output_metadata", 404),
        ("mutable_state", 202),
        ("missing_generation", 409),
        ("foreign_director_run", 404),
        ("missing_director_run", 409),
    ],
)
async def test_generate_all_replay_reauthorizes_every_cached_reference(
    session_factory, tmp_path, cached_kind, expected_status
):
    from apps.api.app.schemas.api import GenerateAll
    from tests.integration.test_generate_all import batch_state, submit

    user_id, _, video_id, _, _ = await batch_state(session_factory)
    payload = GenerateAll()
    key = f"batch-replay-{cached_kind}"
    await submit(session_factory, tmp_path, user_id, video_id, payload, key=key)
    foreign_resources = (
        await seed_replay_world(session_factory)
        if cached_kind
        in {"foreign_parent", "wrong_workflow", "foreign_snapshot", "foreign_output_metadata"}
        else None
    )
    foreign_ids = None
    if cached_kind in {"foreign_generation", "foreign_director_run"}:
        async with session_factory() as session:
            generation = await session.scalar(select(SceneGeneration).where(
                SceneGeneration.video_id == video_id
            ))
            assert generation is not None
            workflow_id = generation.workflow_id
        foreign_ids = await seed_foreign_video_graph(session_factory, workflow_id)
    elif cached_kind == "same_owner_other_video":
        async with session_factory() as session:
            generation = await session.scalar(select(SceneGeneration).where(
                SceneGeneration.video_id == video_id
            ))
            assert generation is not None
            workflow_id = generation.workflow_id
        foreign_ids = await seed_foreign_video_graph(
            session_factory, workflow_id, existing_owner_id=user_id
        )
    async with session_factory() as session, session.begin():
        record = await session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.operation == f"generate-all:{video_id}",
                IdempotencyKey.key == key,
            )
        )
        assert record is not None
        replay = deepcopy(record.response)
        if cached_kind in {"foreign_generation", "same_owner_other_video"}:
            assert foreign_ids is not None
            other_generation = await session.get(SceneGeneration, foreign_ids[3])
            assert other_generation is not None
            entry = GenerationDTO.model_validate(other_generation).model_dump(mode="json")
            # Preserve the accepted batch scene/configuration envelope so the
            # replay reaches row-level ownership reauthorization.
            accepted_entry = replay["generations"][0]
            entry["scene_id"] = accepted_entry["scene_id"]
            entry["input_snapshot"] = deepcopy(accepted_entry["input_snapshot"])
            replay["generations"] = [entry]
            replay["execution_groups"] = [
                {
                    "execution_scope": "single_scene",
                    "scene_ids": [foreign_ids[2]],
                    "generation_ids": [foreign_ids[3]],
                    "director_run_id": None,
                }
            ]
        elif cached_kind == "missing_generation":
            entry = deepcopy(replay["generations"][0])
            entry["id"] = "00000000-0000-0000-0000-000000000002"
            replay["generations"] = [entry]
            replay["execution_groups"][0]["generation_ids"] = [entry["id"]]
        elif cached_kind in {
            "foreign_parent",
            "wrong_workflow",
            "foreign_snapshot",
            "foreign_output_metadata",
            "mutable_state",
        }:
            entry = deepcopy(replay["generations"][0])
            if cached_kind == "foreign_parent":
                assert foreign_resources is not None
                entry["parent_generation_id"] = foreign_resources["editorb_generation"]
            elif cached_kind == "wrong_workflow":
                assert foreign_resources is not None
                entry["workflow_id"] = foreign_resources["editorb_workflow"]
            elif cached_kind == "foreign_snapshot":
                assert foreign_resources is not None
                # Keep the accepted preview hashes while storing the foreign
                # reference in both canonical row and cached DTO. This isolates
                # the ownership check from malformed-cache rejection.
                generation = await session.get(SceneGeneration, entry["id"])
                assert generation is not None
                snapshot = deepcopy(generation.input_snapshot)
                snapshot["assets"] = [{"id": foreign_resources["editorb_asset"]}]
                generation.input_snapshot = snapshot
                entry["input_snapshot"] = snapshot
            elif cached_kind == "foreign_output_metadata":
                assert foreign_resources is not None
                entry["output_metadata"] = {
                    "artifacts": [{"asset_id": foreign_resources["editorb_asset"]}]
                }
            else:
                entry["status"] = "FAILED"
                entry["error_message"] = "stale batch cache"
                entry["output_metadata"] = {"cached_marker": "stale"}
            replay["generations"] = [entry]
        elif cached_kind in {"foreign_director_run", "missing_director_run"}:
            run_id = (
                foreign_ids[4]
                if cached_kind == "foreign_director_run" and foreign_ids
                else "00000000-0000-0000-0000-000000000003"
            )
            replay["director_run_id"] = run_id
            replay["execution_groups"][0]["director_run_id"] = run_id
            replay["execution_groups"][0]["execution_scope"] = "aggregate"
        record.response = replay

    before = await row_counts(session_factory)
    before_state = await replay_database_state(session_factory, key)
    from apps.api.app.core.errors import AppError

    if expected_status == 202:
        result = await submit(session_factory, tmp_path, user_id, video_id, payload, key=key)
        assert result.generations[0].status == "CREATED"
        assert result.generations[0].error_message is None
        assert _non_null_output_metadata(result.generations[0].output_metadata) == {}
    else:
        with pytest.raises(AppError) as caught:
            await submit(session_factory, tmp_path, user_id, video_id, payload, key=key)
        assert caught.value.status_code == expected_status
    assert await row_counts(session_factory) == before
    assert await replay_database_state(session_factory, key) == before_state
