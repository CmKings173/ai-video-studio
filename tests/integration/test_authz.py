from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from apps.api.app.core.config import get_settings
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import User
from apps.api.app.db.session import get_session
from apps.api.app.main import app


@asynccontextmanager
async def api_client(session_factory):
    async def session_override():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    settings = SimpleNamespace(
        session_cookie_name="studio_session",
        session_hours=24,
        cookie_secure=False,
        csrf_header_name="X-CSRF-Token",
    )
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


async def seed_user(session_factory, *, email: str, password: str, role: str = "EDITOR"):
    async with session_factory() as session, session.begin():
        user = User(
            email=email,
            name=role.title(),
            password_hash=hash_password(password),
            role=role,
        )
        session.add(user)
        await session.flush()
        return user.id


@pytest.mark.asyncio
async def test_login_preserves_password_whitespace_and_returns_opaque_cookie(
    session_factory,
):
    await seed_user(
        session_factory,
        email="editor@example.test",
        password="  correct horse battery  ",
    )
    async with api_client(session_factory) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "EDITOR@EXAMPLE.TEST", "password": "  correct horse battery  "},
        )

        assert response.status_code == 200
        assert response.json()["user"]["email"] == "editor@example.test"
        assert response.json()["csrf_token"]
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=strict" in cookie
        assert "correct horse" not in cookie


@pytest.mark.asyncio
async def test_csrf_and_editor_admin_boundaries_are_enforced(session_factory):
    await seed_user(
        session_factory,
        email="editor@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "editor@example.test", "password": "correct horse battery"},
        )
        csrf = login.json()["csrf_token"]

        denied = await client.post(
            "/api/v1/projects", json={"name": "Campaign", "description": ""}
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "CSRF_INVALID"

        created = await client.post(
            "/api/v1/projects",
            json={"name": "Campaign", "description": ""},
            headers={"X-CSRF-Token": csrf},
        )
        assert created.status_code == 201

        admin_only = await client.get("/api/v1/admin/users")
        assert admin_only.status_code == 403
        assert admin_only.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_error_envelope_is_consistent_and_does_not_echo_password(session_factory):
    await seed_user(
        session_factory,
        email="editor@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "editor@example.test", "password": "wrong-password-secret"},
            headers={"X-Request-ID": "auth-test-request"},
        )

        assert response.status_code == 401
        assert response.headers["X-Request-ID"] == "auth-test-request"
        assert response.json() == {
            "error": {
                "code": "INVALID_CREDENTIALS",
                "message": "Invalid email or password",
                "trace_id": "auth-test-request",
                "details": {},
            }
        }
        assert "wrong-password-secret" not in response.text


@pytest.mark.asyncio
async def test_logout_requires_csrf_and_revokes_server_session(session_factory):
    await seed_user(
        session_factory,
        email="editor@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "editor@example.test", "password": "correct horse battery"},
        )
        csrf = login.json()["csrf_token"]
        assert (await client.post("/api/v1/auth/logout")).status_code == 403
        assert (
            await client.post(
                "/api/v1/auth/logout", headers={"X-CSRF-Token": csrf}
            )
        ).status_code == 204
        assert (await client.get("/api/v1/auth/me")).status_code == 401
