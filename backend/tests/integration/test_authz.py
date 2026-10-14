from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import jwt
import pytest
from sqlalchemy import event, func, select

from apps.api.app.core.config import get_settings
from apps.api.app.core.login_throttle import login_throttle
from apps.api.app.core.security import hash_password
from apps.api.app.db.models import Session as AuthSession
from apps.api.app.db.models import User
from apps.api.app.db.session import get_session
from apps.api.app.main import app


@pytest.fixture(autouse=True)
def clean_login_throttle():
    login_throttle.clear()
    yield
    login_throttle.clear()


@asynccontextmanager
async def api_client(
    session_factory,
    *,
    client=("127.0.0.1", 12345),
    cookie_secure: bool = False,
    jwt_expiration_seconds: int = 2_592_000,
):
    async def session_override():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    settings = SimpleNamespace(
        auth_cookie_name="studio_session",
        jwt_secret="integration-test-only-signing-secret-with-at-least-32-bytes",
        jwt_issuer="ai-video-studio-tests",
        jwt_audience="ai-video-studio-api-tests",
        jwt_expiration_seconds=jwt_expiration_seconds,
        cookie_secure=cookie_secure,
        csrf_header_name="X-CSRF-Token",
    )
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False, client=client)
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
async def test_login_preserves_password_whitespace_and_returns_jwt_cookie(
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
        assert "Max-Age=2592000" in cookie
        assert "Path=/" in cookie
        jwt_cookie = cookie.split(";", 1)[0].split("=", 1)[1]
        assert jwt_cookie.count(".") == 2
        assert "access_token" not in response.json()
        assert "correct horse" not in cookie


@pytest.mark.asyncio
async def test_custom_jwt_lifetime_matches_cookie_max_age(session_factory):
    lifetime = 7_200
    await seed_user(
        session_factory,
        email="custom-expiry@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory, jwt_expiration_seconds=lifetime) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "custom-expiry@example.test", "password": "correct horse battery"},
        )

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert f"Max-Age={lifetime}" in cookie
    token = cookie.split(";", 1)[0].split("=", 1)[1]
    claims = jwt.decode(
        token,
        "integration-test-only-signing-secret-with-at-least-32-bytes",
        algorithms=["HS256"],
        issuer="ai-video-studio-tests",
        audience="ai-video-studio-api-tests",
    )
    assert claims["exp"] - claims["iat"] == lifetime


@pytest.mark.asyncio
async def test_secure_cookie_flag_follows_configuration(session_factory):
    await seed_user(
        session_factory,
        email="secure@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory, cookie_secure=True) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "secure@example.test", "password": "correct horse battery"},
        )

    assert response.status_code == 200
    assert "Secure" in response.headers["set-cookie"]


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

        denied = await client.post("/api/v1/projects", json={"name": "Campaign", "description": ""})
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
async def test_repeated_failed_logins_are_throttled_without_account_enumeration(
    session_factory,
):
    email = f"throttle-{uuid4().hex}@example.test"
    await seed_user(session_factory, email=email, password="correct horse battery")
    async with api_client(session_factory) as client:
        for _ in range(5):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "wrong-password"},
            )
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"

        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrong-password"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "LOGIN_RATE_LIMITED"
        assert "password" not in blocked.text.lower()


@pytest.mark.asyncio
async def test_logout_clears_cookie_without_revoking_a_copied_access_token(session_factory):
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
        logout = await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert logout.status_code == 204
        assert "Max-Age=0" in logout.headers["set-cookie"]
        assert "HttpOnly" in logout.headers["set-cookie"]
        assert "SameSite=strict" in logout.headers["set-cookie"]
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        copied_token = login.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
        client.cookies.set("studio_session", copied_token)
        assert (await client.get("/api/v1/auth/me")).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_cookie", ["expired-jwt", "pre-jwt-opaque"])
async def test_logout_clears_expired_or_pre_jwt_cookie(session_factory, stale_cookie):
    if stale_cookie == "expired-jwt":
        stale_cookie = jwt.encode(
            {
                "sub": "00000000-0000-0000-0000-000000000001",
                "role": "EDITOR",
                "iss": "ai-video-studio-tests",
                "aud": "ai-video-studio-api-tests",
                "iat": 1,
                "exp": 2,
                "csrf": "expired-token-csrf-value-that-is-long-enough",
            },
            "integration-test-only-signing-secret-with-at-least-32-bytes",
            algorithm="HS256",
        )

    async with api_client(session_factory) as client:
        client.cookies.set("studio_session", stale_cookie)
        response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]


@pytest.mark.asyncio
async def test_login_and_authenticated_request_never_read_or_write_session_rows(session_factory):
    await seed_user(
        session_factory,
        email="stateless@example.test",
        password="correct horse battery",
    )
    statements: list[str] = []
    engine = session_factory.kw["bind"].sync_engine

    def capture_sql(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lower())

    event.listen(engine, "before_cursor_execute", capture_sql)
    try:
        async with api_client(session_factory) as client:
            login = await client.post(
                "/api/v1/auth/login",
                json={"email": "stateless@example.test", "password": "correct horse battery"},
            )
            assert login.status_code == 200
            statements.clear()
            response = await client.get("/api/v1/projects")
            assert response.status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture_sql)

    assert not any("sessions" in statement for statement in statements)
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 0


@pytest.mark.asyncio
async def test_csrf_token_from_another_jwt_is_rejected(session_factory):
    await seed_user(
        session_factory,
        email="first@example.test",
        password="correct horse battery",
    )
    await seed_user(
        session_factory,
        email="second@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as first, api_client(session_factory) as second:
        first_login = await first.post(
            "/api/v1/auth/login",
            json={"email": "first@example.test", "password": "correct horse battery"},
        )
        second_login = await second.post(
            "/api/v1/auth/login",
            json={"email": "second@example.test", "password": "correct horse battery"},
        )
        assert first_login.status_code == second_login.status_code == 200

        rejected = await second.post(
            "/api/v1/projects",
            json={"name": "Cross-token", "description": ""},
            headers={"X-CSRF-Token": first_login.json()["csrf_token"]},
        )
        accepted = await second.post(
            "/api/v1/projects",
            json={"name": "Same-token", "description": ""},
            headers={"X-CSRF-Token": second_login.json()["csrf_token"]},
        )

        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "CSRF_INVALID"
        assert accepted.status_code == 201


@pytest.mark.asyncio
async def test_inactive_user_cannot_log_in(session_factory):
    user_id = await seed_user(
        session_factory,
        email="inactive@example.test",
        password="correct horse battery",
    )
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        user.is_active = False

    async with api_client(session_factory) as client:
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "inactive@example.test", "password": "correct horse battery"},
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.asyncio
async def test_issued_jwt_is_revoked_after_account_is_disabled(session_factory):
    user_id = await seed_user(
        session_factory,
        email="disable-after-login@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "disable-after-login@example.test",
                "password": "correct horse battery",
            },
        )
        assert login.status_code == 200
        async with session_factory() as session, session.begin():
            user = await session.get(User, user_id)
            user.is_active = False

        response = await client.get("/api/v1/projects")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_me_rejects_a_valid_jwt_when_the_user_record_no_longer_exists(session_factory):
    user_id = await seed_user(
        session_factory,
        email="deleted-after-login@example.test",
        password="correct horse battery",
    )
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "deleted-after-login@example.test",
                "password": "correct horse battery",
            },
        )
        assert login.status_code == 200
        async with session_factory() as session, session.begin():
            user = await session.get(User, user_id)
            await session.delete(user)

        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_login_throttle_blocks_many_emails_from_one_ip(session_factory):
    async with api_client(session_factory, client=("10.10.10.10", 12345)) as client:
        for index in range(5):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": f"unknown-{index}@example.test", "password": "wrong"},
            )
            assert response.status_code == 401
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": "another@example.test", "password": "wrong"},
        )
        assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_login_throttle_blocks_one_email_from_many_ips(session_factory):
    email = f"identity-{uuid4().hex}@example.test"
    for index in range(5):
        async with api_client(session_factory, client=(f"10.10.20.{index + 1}", 12345)) as client:
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "wrong"},
            )
            assert response.status_code == 401
    async with api_client(session_factory, client=("10.10.20.99", 12345)) as client:
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "wrong"},
        )
    assert blocked.status_code == 429


@pytest.mark.asyncio
async def test_login_success_does_not_clear_ip_throttle_history(session_factory):
    valid_email = f"valid-{uuid4().hex}@example.test"
    await seed_user(session_factory, email=valid_email, password="correct password")

    ip = "10.10.30.30"
    async with api_client(session_factory, client=(ip, 12345)) as client:
        # 4 failed login attempts from same IP across different emails
        for index in range(4):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": f"failed-{index}-{uuid4().hex}@example.test", "password": "wrong"},
            )
            assert response.status_code == 401

        # 1 successful login from the same IP to a valid account
        success = await client.post(
            "/api/v1/auth/login",
            json={"email": valid_email, "password": "correct password"},
        )
        assert success.status_code == 200

        # Further failed attempts from the same IP must still count toward IP threshold
        fifth_failure = await client.post(
            "/api/v1/auth/login",
            json={"email": f"another-1-{uuid4().hex}@example.test", "password": "wrong"},
        )
        assert fifth_failure.status_code == 401

        # 6th attempt is blocked because IP has 5 failures recorded
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": f"another-2-{uuid4().hex}@example.test", "password": "wrong"},
        )
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "LOGIN_RATE_LIMITED"
