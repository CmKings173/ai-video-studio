"""Video routes reject invalid parents before planner, claims, or mutations."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, text, update

from apps.api.app.api import videos as videos_api
from apps.api.app.db.models import Brand, IdempotencyKey, Product, Project, Scene, Video
from apps.api.app.db.session import get_session
from apps.api.app.main import app
from apps.api.app.services import video_dependency_guard
from tests.integration.test_video_scene_ownership import (
    ownership_client as base_ownership_client,
)
from tests.integration.test_video_scene_ownership import (
    row_count,
    seed_ownership_graph,
)


@asynccontextmanager
async def ownership_client(session_factory, principal):
    async def session_override():
        async with session_factory() as session:
            # SQLite's legacy transaction mode does not BEGIN for SELECTs.
            # Explicit BEGIN keeps claim()'s savepoint inside the request's
            # rollback boundary, matching the PostgreSQL transaction contract.
            await session.execute(text("BEGIN"))
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async with base_ownership_client(session_factory, principal) as client:
        app.dependency_overrides[get_session] = session_override
        yield client


def storyboard_payload():
    return {
        "scenes": [
            {"prompt": "First", "duration_seconds": 15},
            {"prompt": "Second", "duration_seconds": 15},
        ],
        "replace": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,key,code",
    [
        (Project, "projects", "PROJECT_NOT_ACTIVE"),
        (Product, "products", "PRODUCT_NOT_ACTIVE"),
        (Brand, "brands", "BRAND_NOT_ACTIVE"),
    ],
)
async def test_archived_parent_blocks_every_video_mutation_before_side_effects(
    session_factory, monkeypatch, model, key, code
):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        parent = await session.get(model, data[key]["A"])
        parent.archived = True
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    calls = []

    async def planner(*args, **kwargs):
        calls.append("planner")
        return {}

    async def forbidden_claim(*args, **kwargs):
        calls.append("claim")
        raise AssertionError("Parent validation must precede idempotency")

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    monkeypatch.setattr(videos_api, "claim", forbidden_claim)
    async with ownership_client(session_factory, principal) as client:
        base = f"/api/v1/videos/{data['videos']['A']}"
        responses = [
            await client.post(f"{base}/storyboard/preview"),
            await client.post(
                f"{base}/storyboard/publish",
                headers={"If-Match": "1", "Idempotency-Key": "archived"},
                json=storyboard_payload(),
            ),
            await client.patch(base, headers={"If-Match": "1"}, json={"title": "Changed"}),
        ]
    assert [response.status_code for response in responses] == [409, 409, 409]
    assert all(response.json()["error"]["code"] == code for response in responses)
    assert calls == []
    assert await row_count(session_factory, IdempotencyKey) == 0
    async with session_factory() as session:
        video = await session.get(Video, data["videos"]["A"])
        scene = await session.get(Scene, data["scenes"]["A"])
        assert (video.revision, video.status, video.title) == (1, "DRAFT", "Video A")
        assert (scene.revision, scene.prompt) == (1, "Scene A")
        assert await row_count(session_factory, Scene) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["EDITOR", "ADMIN"])
@pytest.mark.parametrize(
    "field,key", [("project_id", "projects"), ("product_id", "products"), ("brand_id", "brands")]
)
async def test_cross_owner_storyboard_denied_before_planner_or_claim(
    session_factory, monkeypatch, role, field, key
):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        video = await session.get(Video, data["videos"]["A"])
        setattr(video, field, data[key]["B"])
    principal = {
        "value": SimpleNamespace(
            id=data["users"]["ADMIN" if role == "ADMIN" else "EDITOR_A"], role=role
        )
    }
    calls = []

    async def planner(*args, **kwargs):
        calls.append("planner")
        return {}

    async def forbidden_claim(*args, **kwargs):
        calls.append("claim")
        raise AssertionError("Unauthorized claim")

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    monkeypatch.setattr(videos_api, "claim", forbidden_claim)
    async with ownership_client(session_factory, principal) as client:
        base = f"/api/v1/videos/{data['videos']['A']}/storyboard"
        preview = await client.post(f"{base}/preview")
        publish = await client.post(
            f"{base}/publish",
            json=storyboard_payload(),
            headers={"If-Match": "1", "Idempotency-Key": "foreign"},
        )
    assert preview.status_code == publish.status_code == 404
    assert calls == []
    assert await row_count(session_factory, IdempotencyKey) == 0
    assert await row_count(session_factory, Scene) == 2
    async with session_factory() as session:
        video = await session.get(Video, data["videos"]["A"])
        assert (video.revision, video.status, video.created_by) == (
            1,
            "DRAFT",
            data["users"]["EDITOR_A"],
        )


@pytest.mark.asyncio
async def test_existing_video_accepts_same_owner_explicit_brand_override(
    session_factory, monkeypatch
):
    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        override = Brand(name="Override", created_by=data["users"]["EDITOR_A"])
        session.add(override)
        await session.flush()
        video = await session.get(Video, data["videos"]["A"])
        video.brand_id = override.id
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    calls = []

    async def planner(self, context, total_seconds, *, video_revision):
        calls.append(context["brand"]["name"])
        return {}

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    async with ownership_client(session_factory, principal) as client:
        base = f"/api/v1/videos/{data['videos']['A']}"
        preview = await client.post(f"{base}/storyboard/preview")
        patch = await client.patch(
            base, json={"title": "Override retained"}, headers={"If-Match": "1"}
        )
    assert preview.status_code == patch.status_code == 200
    assert calls == ["Override"]
    async with session_factory() as session:
        video = await session.get(Video, data["videos"]["A"])
        assert (video.revision, video.status, video.created_by) == (
            2,
            "DRAFT",
            data["users"]["EDITOR_A"],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,key,code",
    [
        (Project, "projects", "PROJECT_NOT_ACTIVE"),
        (Product, "products", "PRODUCT_NOT_ACTIVE"),
        (Brand, "brands", "BRAND_NOT_ACTIVE"),
    ],
)
async def test_creation_refreshes_preloaded_parent_after_graph_boundary(
    session_factory, monkeypatch, model, key, code
):
    data = await seed_ownership_graph(session_factory)
    preloaded = []

    async def archive_at_graph_boundary(session):
        preloaded.append(await session.get(model, data[key]["A"]))
        await session.execute(
            update(model)
            .where(model.id == data[key]["A"])
            .values(archived=True)
            .execution_options(synchronize_session=False)
        )
        assert preloaded[0].archived is False

    monkeypatch.setattr(video_dependency_guard, "lock_dependency_graph", archive_at_graph_boundary)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    async with ownership_client(session_factory, principal) as client:
        response = await client.post(
            "/api/v1/videos",
            json={
                "project_id": data["projects"]["A"],
                "product_id": data["products"]["A"],
                "brand_id": data["brands"]["A"],
                "title": "New",
                "brief": "Brief",
                "kind": "QUICK_CLIP",
                "target_duration": 5,
            },
        )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code
    assert len(preloaded) == 1
    assert await row_count(session_factory, Video) == 2
    assert await row_count(session_factory, Scene) == 2


@pytest.mark.asyncio
async def test_valid_storyboard_preview_publish_replay_and_replacement_contracts(
    session_factory, monkeypatch
):
    from apps.api.app.db.models import SceneGeneration

    data = await seed_ownership_graph(session_factory)
    async with session_factory() as session, session.begin():
        await session.execute(
            delete(SceneGeneration).where(SceneGeneration.video_id == data["videos"]["A"])
        )
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    calls = []

    async def planner(self, context, total_seconds, *, video_revision):
        calls.append((context, total_seconds, video_revision))
        return {"scenes": storyboard_payload()["scenes"]}

    monkeypatch.setattr(videos_api.StoryboardService, "plan", planner)
    payload = storyboard_payload()
    payload["replace_scene_revisions"] = {data["scenes"]["A"]: 1}
    async with ownership_client(session_factory, principal) as client:
        base = f"/api/v1/videos/{data['videos']['A']}/storyboard"
        preview = await client.post(f"{base}/preview")
        headers = {"If-Match": "1", "Idempotency-Key": "valid"}
        published = await client.post(f"{base}/publish", json=payload, headers=headers)
        assert published.status_code == 200, published.text
        replay = await client.post(f"{base}/publish", json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == published.json()
        conflict = await client.post(
            f"{base}/publish", json=payload, headers={"If-Match": "2", "Idempotency-Key": "replace"}
        )
        assert conflict.status_code == 412
        assert conflict.json()["error"]["code"] == "STORYBOARD_REPLACE_CONFLICT"
        async with session_factory() as session, session.begin():
            project = await session.get(Project, data["projects"]["A"])
            project.archived = True
        denied_replay = await client.post(f"{base}/publish", json=payload, headers=headers)
        assert denied_replay.status_code == 409
    assert preview.status_code == 200
    assert len(calls) == 1
    assert calls[0][1:] == (30, 1)
    assert await row_count(session_factory, IdempotencyKey) == 1
    assert await row_count(session_factory, Scene, video_id=data["videos"]["A"]) == 2
    async with session_factory() as session:
        video = await session.get(Video, data["videos"]["A"])
        assert (video.revision, video.status) == (2, "STORYBOARD_READY")


@pytest.mark.asyncio
async def test_foreign_storyboard_asset_rejected_before_claim(session_factory, monkeypatch):
    data = await seed_ownership_graph(session_factory)
    principal = {"value": SimpleNamespace(id=data["users"]["EDITOR_A"], role="EDITOR")}
    calls = []

    async def forbidden_claim(*args, **kwargs):
        calls.append("claim")
        raise AssertionError("Unauthorized asset reached claim")

    monkeypatch.setattr(videos_api, "claim", forbidden_claim)
    payload = storyboard_payload()
    payload["scenes"][0]["generation_config"] = {"first_frame_asset_id": data["assets"]["B"]}
    async with ownership_client(session_factory, principal) as client:
        response = await client.post(
            f"/api/v1/videos/{data['videos']['A']}/storyboard/publish",
            json=payload,
            headers={"If-Match": "1", "Idempotency-Key": "foreign-asset"},
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ASSET_NOT_FOUND"
    assert calls == []
    assert await row_count(session_factory, IdempotencyKey) == 0
    assert await row_count(session_factory, Scene) == 2
