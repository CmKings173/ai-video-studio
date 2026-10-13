"""Systemic storyboard boundaries; isolated in-memory DB, no external planner I/O."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from apps.api.app.api import generations as generations_api
from apps.api.app.api import videos as videos_api
from apps.api.app.db.models import Brand, IdempotencyKey, Scene, SceneGeneration, Video
from tests.integration.test_video_route_boundaries import ownership_client, storyboard_payload
from tests.integration.test_video_scene_ownership import row_count, seed_ownership_graph


def actor(data, role="EDITOR_A"):
    return {
        "value": SimpleNamespace(
            id=data["users"][role], role="ADMIN" if role == "ADMIN" else "EDITOR"
        )
    }


async def empty_storyboard(session_factory, data):
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(SceneGeneration).where(SceneGeneration.video_id == data["videos"]["A"])
        )
        await session.execute(delete(Scene).where(Scene.video_id == data["videos"]["A"]))


async def storyboard_state(session_factory, video_id):
    async with session_factory() as session:
        video = await session.get(Video, video_id)
        scenes = (
            await session.scalars(
                select(Scene).where(Scene.video_id == video_id).order_by(Scene.scene_order)
            )
        ).all()
        keys = (await session.scalars(select(IdempotencyKey).order_by(IdempotencyKey.id))).all()
        return (
            (
                video.revision,
                video.status,
                video.created_by,
                video.created_actor_id,
                video.title,
                video.brief,
                deepcopy(video.config),
            ),
            [
                (scene.id, scene.revision, scene.prompt, deepcopy(scene.generation_config))
                for scene in scenes
            ],
            [(key.id, key.request_hash, deepcopy(key.response), key.status_code) for key in keys],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["preview", "publish", "generation", "patch"])
async def test_preview_rejects_archived_product_brand_even_with_active_override(
    session_factory, monkeypatch, operation
):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        inherited = await session.get(Brand, data["brands"]["A"])
        inherited.archived = True
        override = Brand(name="Active override", created_by=data["users"]["EDITOR_A"])
        session.add(override)
        await session.flush()
        video = await session.get(Video, data["videos"]["A"])
        video.brand_id = override.id
    calls = []

    async def planner(*args, **kwargs):
        calls.append("planner")
        return {}

    async def forbidden_claim(*args, **kwargs):
        calls.append("claim")
        raise AssertionError("Archived parent reached idempotency")

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    monkeypatch.setattr(videos_api, "claim", forbidden_claim)
    monkeypatch.setattr(generations_api, "claim", forbidden_claim)
    before = await storyboard_state(session_factory, data["videos"]["A"])
    generation_count = await row_count(session_factory, SceneGeneration)
    async with ownership_client(session_factory, actor(data)) as client:
        if operation == "patch":
            response = await client.patch(
                f"/api/v1/videos/{data['videos']['A']}",
                json={"title": "Rejected mutation"},
                headers={"If-Match": "1"},
            )
        elif operation == "generation":
            response = await client.post(
                f"/api/v1/scenes/{data['scenes']['A']}/generations",
                json={},
                headers={"Idempotency-Key": "archived-generation"},
            )
        else:
            response = await client.post(
                f"/api/v1/videos/{data['videos']['A']}/storyboard/{operation}",
                **(
                    {
                        "json": storyboard_payload(),
                        "headers": {"If-Match": "1", "Idempotency-Key": "archived-publish"},
                    }
                    if operation == "publish"
                    else {}
                ),
            )
    assert calls == [], response.text
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BRAND_NOT_ACTIVE"
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before
    assert await row_count(session_factory, SceneGeneration) == generation_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "first_frame_asset_id",
        "last_frame_asset_id",
        "source_video_asset_id",
        "reference_image_asset_ids",
        "reference_video_asset_ids",
        "reference_audio_asset_ids",
    ],
)
@pytest.mark.parametrize("role", ["EDITOR_A", "ADMIN"])
async def test_all_foreign_storyboard_reference_fields_denied_before_claim(
    session_factory, monkeypatch, field, role
):
    data = await seed_ownership_graph(session_factory)
    calls = []

    async def forbidden_claim(*args, **kwargs):
        calls.append("claim")
        raise AssertionError("Foreign reference reached claim")

    monkeypatch.setattr(videos_api, "claim", forbidden_claim)
    payload = storyboard_payload()
    foreign = data["assets"]["B"]
    payload["scenes"][0]["generation_config"] = {
        field: [foreign] if field.startswith("reference_") else foreign
    }
    before = await storyboard_state(session_factory, data["videos"]["A"])
    async with ownership_client(session_factory, actor(data, role)) as client:
        response = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/storyboard/publish",
            json=payload,
            headers={"If-Match": "1", "Idempotency-Key": "foreign-field"},
        )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "ASSET_NOT_FOUND"
    assert calls == []
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before
    assert await row_count(session_factory, IdempotencyKey) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["foreign_video", "foreign_scene", "private_metadata"])
async def test_storyboard_replay_does_not_trust_cached_identity_or_content(session_factory, tamper):
    data = await seed_ownership_graph(session_factory)
    await empty_storyboard(session_factory, data)
    payload = storyboard_payload()
    headers = {"If-Match": "1", "Idempotency-Key": "cache-identity"}
    url = f"/api/v1/videos/{data['videos']['A']}/storyboard/publish"
    async with ownership_client(session_factory, actor(data)) as client:
        original = await client.post(url, json=payload, headers=headers)
        assert original.status_code == 200, original.text
        async with session_factory() as session, session.begin():
            key = await session.scalar(select(IdempotencyKey))
            cached = deepcopy(key.response)
            if tamper == "foreign_video":
                cached["id"] = data["videos"]["B"]
                cached["project_id"] = data["projects"]["B"]
            elif tamper == "foreign_scene":
                cached["scenes"][0]["id"] = data["scenes"]["B"]
                cached["scenes"][0]["video_id"] = data["videos"]["B"]
            else:
                cached["config"] = {"private": "unverified-cache-content"}
            key.response = cached
        before = await storyboard_state(session_factory, data["videos"]["A"])
        replay = await client.post(url, json=payload, headers=headers)
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before
    assert await row_count(session_factory, IdempotencyKey) == 1
    # Reject a forged identity, or serve canonical data; never echo forged DTOs.
    assert replay.status_code in (200, 404, 409), replay.text
    if replay.status_code == 200:
        assert replay.json() == original.json()


@pytest.mark.asyncio
async def test_storyboard_replay_reauthorizes_canonical_scene_references(session_factory):
    data = await seed_ownership_graph(session_factory)
    await empty_storyboard(session_factory, data)
    payload = storyboard_payload()
    headers = {"If-Match": "1", "Idempotency-Key": "canonical-reference"}
    url = f"/api/v1/videos/{data['videos']['A']}/storyboard/publish"
    async with ownership_client(session_factory, actor(data)) as client:
        original = await client.post(url, json=payload, headers=headers)
        assert original.status_code == 200, original.text
        async with session_factory() as session, session.begin():
            scene = await session.get(Scene, original.json()["scenes"][0]["id"])
            scene.generation_config = {"first_frame_asset_id": data["assets"]["B"]}
        before = await storyboard_state(session_factory, data["videos"]["A"])
        replay = await client.post(url, json=payload, headers=headers)
    assert replay.status_code == 404, replay.text
    assert replay.json()["error"]["code"] == "ASSET_NOT_FOUND"
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper,code,status",
    [
        ("missing_scene", "IDEMPOTENCY_RESOURCE_MISSING", 409),
        ("foreign_scene_id", "SCENE_NOT_FOUND", 404),
        ("duplicate_scene", "IDEMPOTENCY_RESOURCE_MISSING", 409),
        ("omitted_scene", "IDEMPOTENCY_RESOURCE_MISSING", 409),
        ("cached_foreign_selection", None, 200),
    ],
)
async def test_storyboard_replay_missing_or_foreign_resources_fail_closed(
    session_factory, tamper, code, status
):
    data = await seed_ownership_graph(session_factory)
    await empty_storyboard(session_factory, data)
    payload = storyboard_payload()
    headers = {"If-Match": "1", "Idempotency-Key": "fail-closed"}
    url = f"/api/v1/videos/{data['videos']['A']}/storyboard/publish"
    async with ownership_client(session_factory, actor(data)) as client:
        original = await client.post(url, json=payload, headers=headers)
        assert original.status_code == 200, original.text
        async with session_factory() as session, session.begin():
            key = await session.scalar(select(IdempotencyKey))
            cached = deepcopy(key.response)
            if tamper == "missing_scene":
                scene = await session.get(Scene, cached["scenes"][0]["id"])
                await session.delete(scene)
            elif tamper == "foreign_scene_id":
                # Keep claimed parent unchanged; the DB must detect the false binding.
                cached["scenes"][0]["id"] = data["scenes"]["B"]
            elif tamper == "duplicate_scene":
                cached["scenes"][1] = deepcopy(cached["scenes"][0])
            elif tamper == "omitted_scene":
                cached["scenes"].pop()
            else:
                cached["scenes"][0]["selected_generation_id"] = data["generations"]["B"]
            key.response = cached
        before = await storyboard_state(session_factory, data["videos"]["A"])
        replay = await client.post(url, json=payload, headers=headers)
    assert replay.status_code == status, replay.text
    if status == 200:
        assert replay.json() == original.json()
    else:
        assert replay.json()["error"]["code"] == code
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before


@pytest.mark.asyncio
async def test_storyboard_replay_returns_legitimate_advanced_scene_state(session_factory):
    data = await seed_ownership_graph(session_factory)
    await empty_storyboard(session_factory, data)
    payload = storyboard_payload()
    headers = {"If-Match": "1", "Idempotency-Key": "advanced-state"}
    url = f"/api/v1/videos/{data['videos']['A']}/storyboard/publish"
    async with ownership_client(session_factory, actor(data)) as client:
        original = await client.post(url, json=payload, headers=headers)
        assert original.status_code == 200, original.text
        scene_id = original.json()["scenes"][0]["id"]
        edited = await client.patch(
            f"/api/v1/scenes/{scene_id}",
            json={
                "prompt": "Legitimate later edit",
                "generation_config": {"first_frame_asset_id": data["assets"]["A"]},
            },
            headers={"If-Match": "1"},
        )
        assert edited.status_code == 200, edited.text
        canonical = await client.get(f"/api/v1/videos/{data['videos']['A']}")
        assert canonical.status_code == 200, canonical.text
        before = await storyboard_state(session_factory, data["videos"]["A"])
        replay = await client.post(url, json=payload, headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json() == canonical.json()
    assert replay.json()["scenes"][0]["prompt"] == "Legitimate later edit"
    assert replay.json()["scenes"][0]["revision"] == 2
    assert await storyboard_state(session_factory, data["videos"]["A"]) == before
    assert await row_count(session_factory, IdempotencyKey) == 1


@pytest.mark.asyncio
async def test_storyboard_replay_preserves_pending_scene_edits(session_factory):
    data = await seed_ownership_graph(session_factory)
    await empty_storyboard(session_factory, data)
    payload = storyboard_payload()
    principal = actor(data)
    async with ownership_client(session_factory, principal) as client:
        original = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/storyboard/publish",
            json=payload,
            headers={"If-Match": "1", "Idempotency-Key": "dirty-scene"},
        )
        assert original.status_code == 200, original.text
    async with session_factory() as session:
        session.autoflush = True
        video = await session.get(Video, data["videos"]["A"])
        scene = await session.get(Scene, original.json()["scenes"][0]["id"])
        scene.prompt = "Pending legitimate edit"
        result = await videos_api._storyboard_replay(
            session, video, original.json(), principal["value"], len(payload["scenes"])
        )
        assert result.model_dump(mode="json") == original.json()
        assert scene.prompt == "Pending legitimate edit"
        assert scene in session.dirty
        assert list(session.dirty) == [scene]
        await session.rollback()


@pytest.mark.asyncio
async def test_admin_delegation_preserves_owner_actor_and_valid_storyboard(
    session_factory, monkeypatch
):
    data = await seed_ownership_graph(session_factory)
    calls = []

    async def planner(self, context, total_seconds, *, video_revision):
        calls.append((context["product"]["name"], total_seconds, video_revision))
        return {"scenes": storyboard_payload()["scenes"]}

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    principal = actor(data, "ADMIN")
    async with ownership_client(session_factory, principal) as client:
        created = await client.post(
            "/api/v1/videos",
            json={
                "project_id": data["projects"]["B"],
                "product_id": data["products"]["B"],
                "title": "Delegated storyboard",
                "brief": "Brief",
                "kind": "LONG_VIDEO",
                "target_duration": 30,
            },
        )
        assert created.status_code == 201, created.text
        video_id = created.json()["id"]
        async with session_factory() as session:
            video = await session.get(Video, video_id)
            assert video.created_by == data["users"]["EDITOR_B"]
            assert video.created_actor_id == data["users"]["ADMIN"]
        principal["value"] = actor(data, "EDITOR_B")["value"]
        preview = await client.post(f"/api/v1/videos/{video_id}/storyboard/preview")
        assert preview.status_code == 200, preview.text
        headers = {"If-Match": "1", "Idempotency-Key": "delegated"}
        payload = storyboard_payload()
        published = await client.post(
            f"/api/v1/videos/{video_id}/storyboard/publish", json=payload, headers=headers
        )
        replay = await client.post(
            f"/api/v1/videos/{video_id}/storyboard/publish", json=payload, headers=headers
        )
        assert published.status_code == replay.status_code == 200
        assert replay.json() == published.json()
        principal["value"] = actor(data, "EDITOR_A")["value"]
        denied = await client.get(f"/api/v1/videos/{video_id}")
        assert denied.status_code == 404
    assert calls == [("Product B", 30, 1)]
    assert await row_count(session_factory, Scene, video_id=video_id) == 2
    assert await row_count(session_factory, IdempotencyKey) == 1
