from types import SimpleNamespace

import httpx
import pytest

from apps.api.app.db.models import Brand, Product, Project, Scene, User, Video
from apps.api.app.services.storyboard_service import StoryboardService


@pytest.mark.asyncio
@pytest.mark.parametrize(("duration", "count"), [(30, 5), (60, 8)])
async def test_deterministic_storyboard_is_publishable(duration, count):
    result = await StoryboardService().plan(
        {"brief": "Launch", "product": {"name": "Atlas"}}, duration
    )

    assert len(result["scenes"]) == count
    assert sum(scene["duration_seconds"] for scene in result["scenes"]) == duration
    assert [scene["scene_order"] for scene in result["scenes"]] == list(range(count))


@pytest.mark.asyncio
async def test_storyboard_planner_cannot_override_operator_llm_endpoint():
    async def handler(request):
        assert request.url.host == "allowed-llm.test"
        assert request.headers["Authorization"] == "Bearer operator-secret"
        return httpx.Response(
            503,
            json={"error": "offline"},
        )

    settings = SimpleNamespace(
        llm_base_url="https://allowed-llm.test/v1",
        llm_model="operator-model",
        llm_api_key="operator-secret",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await StoryboardService(settings=settings, client=client).plan(
            {
                "brief": "Launch",
                "product": {"name": "Atlas"},
                "planner": {
                    "enabled": True,
                    "base_url": "http://169.254.169.254/latest/meta-data",
                    "api_key": "editor-controlled",
                },
            },
            30,
        )
    finally:
        await client.aclose()

    assert result["source"] == "deterministic_fallback"
    assert result["warnings"] == ["planner_failed"]


def publish_scenes(result):
    # Match the Generation Editor's preview -> publish projection.
    fields = ("scene_order", "prompt", "negative_prompt", "duration_seconds", "spec")
    return [{key: scene[key] for key in fields} for scene in result["scenes"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [30, 60])
@pytest.mark.parametrize("case", ["brief", "suffix", "product"])
async def test_generated_content_obeys_canonical_publish_schema(duration, case):
    from apps.api.app.schemas.api import StoryboardPublish

    context = {"brief": "Launch", "product": {"name": "Atlas", "context": {"text": "x" * 50000}}}
    if case == "brief":
        context["brief"] = "b" * 10001
    elif case == "suffix":
        context["brief"] = "b" * 20000
    else:
        context["product"]["name"] = "p" * 30000
    result = await StoryboardService().plan(context, duration)
    payload = StoryboardPublish(scenes=publish_scenes(result))
    assert len(payload.scenes) == (5 if duration == 30 else 8)
    assert sum(scene.duration_seconds for scene in payload.scenes) == duration
    assert [scene.scene_order for scene in payload.scenes] == list(range(len(payload.scenes)))
    assert all(scene.spec.continuity == "CUT" for scene in payload.scenes)
    for index, scene in enumerate(payload.scenes):
        purpose = StoryboardService.PURPOSES[min(index, 4)].replace("_", " ").lower()
        assert f". {purpose} featuring " in scene.prompt
        assert scene.prompt.endswith(".")


@pytest.mark.asyncio
async def test_valid_generated_text_is_not_truncated():
    from apps.api.app.schemas.api import StoryboardPublish

    brief = "b" * 10000
    result = await StoryboardService().plan({"brief": brief, "product": {"name": "Atlas"}}, 30)
    payload = StoryboardPublish(scenes=publish_scenes(result))
    assert payload.scenes[0].spec.description == brief
    assert payload.scenes[0].spec.subject == "Atlas"
    assert payload.scenes[0].prompt == f"{brief}. hook featuring Atlas."


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [30, 60])
async def test_valid_llm_output_and_long_context_are_preserved(duration):
    import json

    from apps.api.app.schemas.api import StoryboardPublish

    context = {
        "brief": "b" * 20000,
        "product": {"name": "Atlas", "context": {"nested": ["p" * 50000]}},
        "brand": {"context": {"nested": ["b" * 50000]}},
        "planner": {"enabled": True},
    }
    scenes = StoryboardService().deterministic({"brief": "Launch"}, duration).scenes
    scenes[0]["prompt"] = "p" * 20000
    scenes[0]["spec"]["description"] = "d" * 10000

    async def handler(request):
        body = json.loads(request.content)
        supplied = json.loads(body["messages"][1]["content"])
        for field in ("brief", "product", "brand"):
            assert supplied[field] == context[field]
        content = json.dumps({"scenes": scenes})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    settings = SimpleNamespace(llm_base_url="https://llm.test/v1", llm_model="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StoryboardService(settings, client).plan(context, duration)
    assert result["source"] == "llm"
    assert result["warnings"] == []
    assert result["scenes"] == scenes
    StoryboardPublish(scenes=publish_scenes(result))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [None]},
        {"choices": [{"message": {"content": "not JSON"}}]},
        {"choices": [{"message": {"content": "[]"}}]},
        {"choices": [{"message": {"content": '{"scenes": null}'}}]},
    ],
)
async def test_malformed_provider_responses_keep_existing_fallback(body):
    from apps.api.app.schemas.api import StoryboardPublish

    async def handler(request):
        return httpx.Response(200, json=body)

    settings = SimpleNamespace(llm_base_url="https://llm.test/v1", llm_model="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StoryboardService(settings, client).plan({"planner": {"enabled": True}})
    assert result["source"] == "deterministic_fallback"
    assert result["warnings"] == ["planner_failed"]
    StoryboardPublish(scenes=publish_scenes(result))


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [30, 60])
@pytest.mark.parametrize(
    "invalid",
    [
        "description",
        "prompt",
        "negative_prompt",
        "continuity",
        "order",
        "duration",
        "shape",
        "extra",
        "missing_spec",
    ],
)
async def test_invalid_llm_scene_falls_back_to_publishable_storyboard(duration, invalid):
    import json
    from copy import deepcopy

    from apps.api.app.schemas.api import StoryboardPublish

    scenes = deepcopy(StoryboardService().deterministic({"brief": "Launch"}, duration).scenes)
    if invalid == "description":
        scenes[-1]["spec"]["description"] = "x" * 10001
    elif invalid == "prompt":
        scenes[-1]["prompt"] = "x" * 20001
    elif invalid == "negative_prompt":
        scenes[-1]["negative_prompt"] = "x" * 10001
    elif invalid == "continuity":
        scenes[-1]["spec"]["continuity"] = "JUMP"
    elif invalid == "order":
        scenes[-1]["scene_order"] = len(scenes) - 1 + 0.5
    elif invalid == "duration":
        scenes[-1]["duration_seconds"] = float("nan")
    elif invalid == "shape":
        scenes[-1] = None
    elif invalid == "extra":
        scenes[-1]["unexpected"] = "not publishable"
    else:
        scenes[-1].pop("spec")

    async def handler(request):
        content = json.dumps({"scenes": scenes})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    settings = SimpleNamespace(llm_base_url="https://llm.test/v1", llm_model="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await StoryboardService(settings, client).plan(
            {"brief": "b" * 20000, "planner": {"enabled": True}}, duration
        )
    assert result["source"] == "deterministic_fallback"
    assert result["warnings"] == ["planner_failed"]
    payload = StoryboardPublish(scenes=publish_scenes(result))
    assert sum(scene.duration_seconds for scene in payload.scenes) == duration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        KeyError("bug"),
        TypeError("bug"),
        ValueError("bug"),
        IndexError("bug"),
    ],
)
async def test_planner_programming_errors_are_not_hidden(monkeypatch, error):
    async def broken(*args):
        raise error

    service = StoryboardService()
    monkeypatch.setattr(service, "_llm", broken)
    with pytest.raises(type(error), match="bug"):
        await service.plan({"planner": {"enabled": True}}, 30)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [30, 60])
@pytest.mark.parametrize("planner_enabled", [False, True])
async def test_actual_preview_publish_api_with_long_context(
    session_factory, duration, planner_enabled, monkeypatch
):
    import json

    from fastapi import FastAPI

    from apps.api.app.api.deps import require_csrf
    from apps.api.app.api.videos import router
    from apps.api.app.core.config import Settings, get_settings
    from apps.api.app.db.session import get_session

    async def handler(request):
        supplied = json.loads(json.loads(request.content)["messages"][1]["content"])
        assert supplied["brief"] == "b" * 20000
        assert supplied["product"]["context"] == {"nested": ["x" * 50000]}
        scenes = StoryboardService().deterministic({"brief": "Launch"}, duration).scenes
        # Correct count/order/duration, but one scene violates publish content bounds.
        scenes[-1]["spec"]["description"] = "x" * 10001
        content = json.dumps({"scenes": scenes})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with session_factory() as session:
        user = User(email="storyboard@test.local", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", created_by=user.id)
        brand = Brand(
            name="Brand",
            description="x" * 20000,
            context={"nested": ["x" * 50000]},
            created_by=user.id,
        )
        session.add_all([project, brand])
        await session.flush()
        product = Product(
            name="p" * 255,
            description="x" * 20000,
            context={"nested": ["x" * 50000]},
            brand_id=brand.id,
            created_by=user.id,
        )
        session.add(product)
        await session.flush()
        video = Video(
            project_id=project.id,
            product_id=product.id,
            title="Launch",
            brief="b" * 20000,
            kind="LONG_VIDEO",
            target_duration=duration,
            config={"storyboard_planner": {"enabled": planner_enabled}},
            created_by=user.id,
        )
        session.add(video)
        await session.commit()
        video_id = video.id
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")

        async def test_session():
            yield session

        app.dependency_overrides[get_session] = test_session
        app.dependency_overrides[require_csrf] = lambda: user
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as llm_client:
            llm_settings = SimpleNamespace(llm_base_url="https://llm.test/v1", llm_model="test")
            monkeypatch.setattr(
                "apps.api.app.api.videos.StoryboardService",
                lambda settings: StoryboardService(llm_settings, llm_client),
            )
            await _assert_preview_publish(app, session, video_id, duration, planner_enabled)


async def _assert_preview_publish(app, session, video_id, duration, planner_enabled):
    from copy import deepcopy

    from sqlalchemy import func, select

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        url = f"/api/v1/videos/{video_id}/storyboard"
        response = await client.post(f"{url}/preview")
        assert response.status_code == 200
        preview = response.json()
        assert preview["source"] == (
            "deterministic_fallback" if planner_enabled else "deterministic"
        )
        assert preview["warnings"] == (["planner_failed"] if planner_enabled else [])
        payload = {"scenes": publish_scenes(preview)}
        headers = {"If-Match": str(preview["video_revision"]), "Idempotency-Key": "publish"}
        invalid = deepcopy(payload)
        invalid["scenes"][-1]["spec"]["description"] = "x" * 10001
        rejected = await client.post(f"{url}/publish", json=invalid, headers=headers)
        assert rejected.status_code == 422
        await session.flush()
        # Count inside the request transaction, before any rollback.
        assert await session.scalar(select(func.count()).select_from(Scene)) == 0
        published = await client.post(f"{url}/publish", json=payload, headers=headers)
        assert published.status_code == 200, published.text
        rows = list(
            (
                await session.scalars(
                    select(Scene).where(Scene.video_id == video_id).order_by(Scene.scene_order)
                )
            ).all()
        )
        assert len(rows) == (5 if duration == 30 else 8)
        assert [row.scene_order for row in rows] == list(range(len(rows)))
        assert sum(row.duration_seconds for row in rows) == duration
        assert [row.prompt for row in rows] == [s["prompt"] for s in payload["scenes"]]
        assert [row.spec for row in rows] == [s["spec"] for s in published.json()["scenes"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
@pytest.mark.parametrize("invalid", ["order", "duration"])
async def test_publish_semantic_rejection_does_not_mutate_scenes(session_factory, replace, invalid):
    from sqlalchemy import select

    from apps.api.app.api.videos import storyboard_publish
    from apps.api.app.core.config import Settings
    from apps.api.app.core.errors import AppError
    from apps.api.app.schemas.api import StoryboardPublish

    async with session_factory() as session:
        user = User(email="publish-preflight@test.local", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", created_by=user.id)
        session.add(project)
        await session.flush()
        video = Video(
            project_id=project.id,
            title="Launch",
            kind="LONG_VIDEO",
            target_duration=30,
            created_by=user.id,
        )
        session.add(video)
        await session.flush()
        existing = (
            [
                Scene(
                    video_id=video.id,
                    scene_order=index,
                    prompt=f"Original {index}",
                    duration_seconds=15,
                    spec={},
                )
                for index in range(2)
            ]
            if replace
            else []
        )
        session.add_all(existing)
        await session.commit()
        before = [(scene.id, scene.prompt, scene.revision) for scene in existing]
        video_before = (video.status, video.revision)
        values = publish_scenes(StoryboardService().deterministic({"brief": "Launch"}, 30).__dict__)
        if invalid == "order":
            values[-1]["scene_order"] = 99
        else:
            values[-1]["duration_seconds"] += 1
        payload = StoryboardPublish(
            scenes=values,
            replace=replace,
            replace_scene_revisions={scene.id: scene.revision for scene in existing},
        )
        with pytest.raises(AppError) as caught:
            await storyboard_publish(
                video.id,
                payload,
                revision=video.revision,
                key="invalid-publish",
                user=user,
                session=session,
                settings=Settings(_env_file=None),
            )
        assert caught.value.code == f"STORYBOARD_{invalid.upper()}_INVALID"
        pending = [row for row in session.new if isinstance(row, Scene)]
        deleted = [row for row in session.deleted if isinstance(row, Scene)]
        # Explicitly flush and inspect this same transaction before any rollback.
        await session.flush()
        rows = list(
            (
                await session.scalars(
                    select(Scene).where(Scene.video_id == video.id).order_by(Scene.scene_order)
                )
            ).all()
        )
        assert [(row.id, row.prompt, row.revision) for row in rows] == before
        assert pending == []
        assert deleted == []
        assert (video.status, video.revision) == video_before
