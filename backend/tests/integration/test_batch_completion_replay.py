"""HTTP retries across real dispatcher selection, with real request transactions."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import Asset, Scene, SceneGeneration, User, Video
from apps.api.app.main import app
from tests.integration.test_authz import api_client
from tests.integration.test_generation_preparation import seed
from workers.dispatcher import Dispatcher


@asynccontextmanager
async def batch_client(factory, tmp_path):
    user, scene = await seed(factory)
    async with factory() as session, session.begin():
        row = await session.get(User, user)
        row.password_hash = hash_password("phase0-retry-password")
        source = await session.get(Scene, scene)
        video = await session.get(Video, source.video_id)
        added = Scene(video_id=video.id, scene_order=1, prompt="Second", spec={})
        session.add(added)
        await session.flush()
        payload = {"scene_ids": [scene, added.id], "expected_video_revision": video.revision,
                   "expected_scene_revisions": {scene: source.revision, added.id: added.revision}}
        video_id = video.id
    settings = Settings(_env_file=None, workspace_root=tmp_path, min_free_disk_bytes=0)
    async with api_client(factory) as client:
        app.dependency_overrides[get_settings] = lambda: settings
        login = await client.post("/api/v1/auth/login", json={
            "email": "editor@example.test", "password": "phase0-retry-password"})
        assert login.status_code == 200
        client.headers.update({"X-CSRF-Token": login.json()["csrf_token"],
                               "Idempotency-Key": "lost-batch-response"})
        yield client, video_id, payload, Dispatcher(factory, None, None, None, settings)


async def finish(dispatcher, generation_id):
    async with dispatcher.factory() as session, session.begin():
        generation = await session.get(SceneGeneration, generation_id)
        generation.status = "COLLECTING"
        generation.claimed_by = dispatcher.owner
        asset = Asset(kind="VIDEO", filename="output.mp4", content_type="video/mp4",
                      object_key=f"completed/{generation.id}.mp4", status="READY",
                      size_bytes=10, created_by=generation.created_by)
        session.add(asset)
        await session.flush()
        asset_id = asset.id
    await dispatcher._finish(generation_id, "COMPLETED", asset_id=asset_id)


@pytest.mark.parametrize("explicit", [True, False])
async def test_http_saved_batch_survives_partial_and_full_dispatcher_completion(
    session_factory, tmp_path, explicit
):
    async with batch_client(session_factory, tmp_path) as (client, video, payload, dispatcher):
        if not explicit:
            payload = {}
        url = f"/api/v1/videos/{video}/generate-all"
        first = await client.post(url, json=payload)
        assert first.status_code == 202, first.text
        original = first.json()
        for generation in original["generations"]:
            await finish(dispatcher, generation["id"])
            retry = await client.post(url, json=payload)
            assert retry.status_code == 202, retry.text
            assert retry.json() == original
        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(SceneGeneration)) == 2


@pytest.mark.parametrize("change", ["prompt", "disable", "added", "config", "revision_only"])
async def test_editor_change_after_dispatcher_completion_rejects_saved_batch(
    session_factory, tmp_path, change
):
    async with batch_client(session_factory, tmp_path) as (client, video, payload, dispatcher):
        url = f"/api/v1/videos/{video}/generate-all"
        first = await client.post(url, json=payload)
        assert first.status_code == 202, first.text
        await finish(dispatcher, first.json()["generations"][0]["id"])
        async with session_factory() as session, session.begin():
            parent = await session.get(Video, video)
            scene = await session.get(Scene, payload["scene_ids"][1])
            if change == "prompt":
                scene.prompt = "Edited"
                scene.revision += 1
            elif change == "disable":
                scene.enabled = False
                scene.revision += 1
            elif change == "added":
                session.add(Scene(video_id=video, scene_order=2, prompt="Added", spec={}))
            elif change == "config":
                parent.config = {"changed": True}
            parent.revision += 1
        retry = await client.post(url, json=payload)
        assert retry.status_code == 409, retry.text
        assert retry.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
