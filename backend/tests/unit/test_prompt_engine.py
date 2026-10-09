from types import SimpleNamespace

import httpx
import pytest

from apps.api.app.services.prompt_engine import PromptEngine

CONTEXT = {
    "brief": "Premium bottle reveal",
    "brand": {"description": "quiet luxury", "context": {"colors": ["gold"]}},
    "product": {"name": "Atlas", "description": "amber glass"},
    "scene": {
        "prompt": "Bottle rotates on stone",
        "spec": {
            "subject": "Atlas bottle",
            "action": "slow rotation",
            "camera": "macro dolly",
            "soundscape": "soft glass resonance",
        },
    },
}


@pytest.mark.asyncio
async def test_deterministic_prompt_has_six_named_sections():
    result = await PromptEngine().compose(CONTEXT)

    assert result.enhanced is False
    assert result.raw_prompt == result.execution_prompt
    assert result.execution_prompt.count("\n") == 5
    assert "subject_definitions:" in result.execution_prompt
    assert "overall_soundscape:" in result.execution_prompt


@pytest.mark.asyncio
async def test_enhancer_failure_falls_back_to_deterministic_prompt():
    async def handler(_request):
        raise httpx.ConnectError("offline")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PromptEngine(
            settings=SimpleNamespace(
                llm_base_url="http://llm.test",
                llm_model="local",
                llm_api_key="",
            ),
            client=client,
        ).compose(
            CONTEXT,
            {"enabled": True},
        )
    finally:
        await client.aclose()

    assert result.execution_prompt == result.raw_prompt
    assert result.warnings == ("enhancer_failed",)


@pytest.mark.asyncio
async def test_enhancer_cannot_override_operator_endpoint_or_api_key():
    async def handler(request):
        assert request.url.host == "allowed-llm.test"
        assert request.headers["Authorization"] == "Bearer operator-secret"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Improved safe prompt"}}]},
        )

    settings = SimpleNamespace(
        llm_base_url="https://allowed-llm.test/v1",
        llm_model="operator-model",
        llm_api_key="operator-secret",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await PromptEngine(settings=settings, client=client).compose(
            CONTEXT,
            {
                "enabled": True,
                "base_url": "http://169.254.169.254/latest/meta-data",
                "api_key": "editor-controlled",
            },
        )
    finally:
        await client.aclose()

    assert result.enhanced is True
    assert result.execution_prompt == "Improved safe prompt"
