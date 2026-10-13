"""Account mutations revoke signed tokens without weakening cookie/CSRF handling."""

import pytest
from sqlalchemy import func, select

from apps.api.app.db.models import Project, User, Video, utcnow
from tests.integration.test_authz import api_client, seed_user


@pytest.mark.asyncio
async def test_explicit_revocation_and_role_change_increment_once(session_factory):
    user_id = await seed_user(
        session_factory, role="ADMIN", email="combined@revocation.test",
        password="synthetic combined mutation password"
    )
    async with session_factory() as session:
        account = await session.get(User, user_id)
        account.auth_version += 1
        account.role = "EDITOR"
        await session.commit()
    async with session_factory() as session:
        assert (await session.get(User, user_id)).auth_version == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["demoted", "disabled", "password_changed"])
async def test_sse_poll_rejects_revoked_account_using_real_database(session_factory, change):
    from apps.api.app.api.deps import principal_from_token
    from apps.api.app.api.events import _snapshot
    from apps.api.app.core.errors import AppError
    from apps.api.app.core.security import create_access_token
    from tests.integration.test_jwt_sse import sse_settings

    user_id = await seed_user(
        session_factory, role="ADMIN", email="sse@revocation.test",
        password="synthetic sse account password"
    )
    async with session_factory() as session:
        session.add(Project(id="revocation-project", name="Test", created_by=user_id))
        await session.flush()
        session.add(Video(id="revocation-video", project_id="revocation-project",
                          title="Test", created_by=user_id))
        await session.commit()
    settings = sse_settings(jwt_expiration_seconds=60)
    token, _ = create_access_token(user_id, "ADMIN", settings)
    principal = principal_from_token(token, settings)
    arguments = dict(principal=principal, generation_cursor=(utcnow(), ""),
                     final_cursor=(utcnow(), ""), active_generation_ids=set(),
                     active_final_ids=set())
    snapshot = await _snapshot(session_factory, "revocation-video", **arguments)
    assert snapshot.events
    async with session_factory() as session:
        user = await session.get(User, user_id)
        if change == "demoted":
            user.role = "EDITOR"
        elif change == "disabled":
            user.is_active = False
        else:
            user.password_hash = "different-synthetic-hash"
        await session.commit()
    with pytest.raises(AppError) as rejected:
        await _snapshot(session_factory, "revocation-video", **arguments)
    assert rejected.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["demoted", "disabled", "password_changed"])
async def test_account_change_revokes_old_jwt_and_keeps_login_logout_csrf(
    session_factory, change
):
    password = "original synthetic account password"
    new_password = "changed synthetic account password"
    target_id = await seed_user(
        session_factory, email="target@revocation.test", password=password, role="ADMIN"
    )
    await seed_user(
        session_factory, email="manager@revocation.test", password=password, role="ADMIN"
    )
    async with api_client(session_factory) as client:
        login = await client.post(
            "/api/v1/auth/login", json={"email": "target@revocation.test", "password": password}
        )
        assert login.status_code == 200
        old_token = client.cookies.get("studio_session")
        old_csrf = login.json()["csrf_token"]
        manager = await client.post(
            "/api/v1/auth/login", json={"email": "manager@revocation.test", "password": password}
        )
        changes = {
            "demoted": {"role": "EDITOR"},
            "disabled": {"is_active": False},
            "password_changed": {"password": new_password},
        }
        edited = await client.patch(
            f"/api/v1/admin/users/{target_id}", json=changes[change],
            headers={"X-CSRF-Token": manager.json()["csrf_token"]},
        )
        assert edited.status_code == 200, edited.text
        client.cookies.clear()
        client.cookies.set("studio_session", old_token, domain="test.local", path="/")
        for route in ("/api/v1/admin/users", "/api/v1/projects", "/api/v1/auth/me"):
            rejected = await client.get(route)
            assert rejected.status_code == 401, (change, route, rejected.text)
        rejected_write = await client.post(
            "/api/v1/projects", json={"name": "must not be created"},
            headers={"X-CSRF-Token": old_csrf},
        )
        assert rejected_write.status_code == 401
        async with session_factory() as session:
            account = await session.get(User, target_id)
            assert account.auth_version == 2
            assert await session.scalar(select(func.count()).select_from(Project)) == 0

        # A revoked cookie can still be cleared, with its original signed CSRF binding.
        logout = await client.post(
            "/api/v1/auth/logout", headers={"X-CSRF-Token": old_csrf}
        )
        assert logout.status_code == 204
        assert not client.cookies.get("studio_session")
        fresh = await client.post(
            "/api/v1/auth/login",
            json={"email": "target@revocation.test",
                  "password": new_password if change == "password_changed" else password},
        )
        if change == "disabled":
            assert fresh.status_code == 403
            return
        assert fresh.status_code == 200
        assert (await client.get("/api/v1/projects")).status_code == 200
        assert (await client.get("/api/v1/admin/users")).status_code == (
            403 if change == "demoted" else 200
        )
        no_csrf = await client.post("/api/v1/projects", json={"name": "blocked without CSRF"})
        assert no_csrf.status_code == 403
        accepted = await client.post(
            "/api/v1/projects", json={"name": "fresh token"},
            headers={"X-CSRF-Token": fresh.json()["csrf_token"]},
        )
        assert accepted.status_code == 201, accepted.text
