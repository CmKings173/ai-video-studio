from types import SimpleNamespace

import httpx
import pytest

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
