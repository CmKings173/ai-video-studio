"""Five-client HTTP auth acceptance; deployment/browser UAT is a separate gate."""

from contextlib import AsyncExitStack

import httpx
import pytest

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.main import app
from tests.integration.test_authz import api_client, seed_user

TOKEN = "five-editor-fixture-internal-token-2026"
PASSWORD = "fixture-only-five-editor-password"


@pytest.fixture(autouse=True)
def fixed_throttle_clock(monkeypatch):
    login_throttle.clear()
    monkeypatch.setattr(login_throttle, "_clock", lambda: 100.0)
    yield
    login_throttle.clear()


async def editor_clients(stack):
    clients = []
    for index in range(5):
        clients.append(
            await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app, client=("172.18.0.5", 12345)),
                    base_url="http://test",
                    headers={
                        "x-studio-client-ip": f"192.0.2.{index + 1}",
                        "x-studio-proxy-token": TOKEN,
                    },
                )
            )
        )
    return clients


async def test_five_sessions_stay_distinct_when_one_editor_is_throttled(session_factory):
    emails = [f"fixture-editor-{index}@example.test" for index in range(5)]
    user_ids = [
        await seed_user(session_factory, email=email, password=PASSWORD) for email in emails
    ]
    async with api_client(session_factory), AsyncExitStack() as stack:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            internal_proxy_token=TOKEN,
            cookie_secure=False,
        )
        clients = await editor_clients(stack)
        tokens, csrf_tokens = [], []
        for client, email, user_id in zip(clients, emails, user_ids, strict=True):
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": PASSWORD,
                },
            )
            assert response.status_code == 200
            assert response.json()["user"]["id"] == user_id
            tokens.append(client.cookies.get("studio_session"))
            csrf_tokens.append(response.json()["csrf_token"])
        assert None not in tokens
        assert len(set(tokens)) == len(set(csrf_tokens)) == 5

        for _ in range(5):
            response = await clients[0].post(
                "/api/v1/auth/login",
                json={
                    "email": emails[0],
                    "password": "wrong-fixture-password",
                },
            )
            assert response.status_code == 401
        blocked = await clients[0].post(
            "/api/v1/auth/login",
            json={
                "email": emails[0],
                "password": PASSWORD,
            },
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "LOGIN_RATE_LIMITED"

        for client, email in zip(clients[1:], emails[1:], strict=True):
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": PASSWORD,
                },
            )
            assert response.status_code == 200
        for client, user_id in zip(clients, user_ids, strict=True):
            response = await client.get("/api/v1/auth/me")
            assert response.status_code == 200
            assert response.json()["id"] == user_id

        response = await clients[0].post(
            "/api/v1/auth/logout",
            headers={
                "X-CSRF-Token": csrf_tokens[0],
            },
        )
        assert response.status_code == 204
        assert (await clients[0].get("/api/v1/auth/me")).status_code == 401
        for client in clients[1:]:
            assert (await client.get("/api/v1/auth/me")).status_code == 200


async def test_five_trusted_ips_cannot_bypass_shared_identity_throttle(session_factory):
    email = "fixture-target@example.test"
    await seed_user(session_factory, email=email, password=PASSWORD)
    async with api_client(session_factory), AsyncExitStack() as stack:
        app.dependency_overrides[get_settings] = lambda: Settings(
            _env_file=None,
            internal_proxy_token=TOKEN,
            cookie_secure=False,
        )
        clients = await editor_clients(stack)
        for client in clients:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": "wrong-fixture-password",
                },
            )
            assert response.status_code == 401
        for client in clients:
            response = await client.post(
                "/api/v1/auth/login",
                json={
                    "email": email,
                    "password": PASSWORD,
                },
            )
            assert response.status_code == 429
            assert response.json()["error"]["code"] == "LOGIN_RATE_LIMITED"
            assert client.cookies.get("studio_session") is None
