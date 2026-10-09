from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from apps.api.app.api.assets import router, store
from apps.api.app.api.deps import require_csrf, require_editor
from apps.api.app.core.config import get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset
from apps.api.app.db.session import get_session
from apps.api.app.integrations.media import ALLOWED_TYPES
from tests.integration.test_asset_validation_worker import read, seed, settings


def app_for(factory, config, user_id, upload_storage=None):
    app = FastAPI()
    app.include_router(router)

    async def session_dependency():
        async with factory() as session, session.begin():
            yield session

    def forbidden_storage():
        pytest.fail("Complete request must not initialize or invoke storage")

    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_settings] = lambda: config
    app.dependency_overrides[require_csrf] = lambda: SimpleNamespace(id=user_id)
    app.dependency_overrides[require_editor] = lambda: SimpleNamespace(id=user_id)
    app.dependency_overrides[store] = (
        (lambda: upload_storage) if upload_storage is not None else forbidden_storage
    )

    @app.exception_handler(AppError)
    async def error_handler(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    return app


class PresignStore:
    async def presign_upload(self, key, content_type):
        return {
            "url": "https://storage.test/upload",
            "fields": {"key": key, "Content-Type": content_type},
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "content_type"),
    [
        ("PRODUCT_IMAGE", "image/png"),
        ("PROJECT_REFERENCE", "image/jpeg"),
        ("SOURCE_VIDEO", "video/mp4"),
        ("REFERENCE_VIDEO", "video/webm"),
        ("REFERENCE_AUDIO", "audio/wav"),
        ("BACKGROUND_AUDIO", "audio/ogg"),
    ],
)
async def test_upload_http_accepts_each_role_with_its_media_kind(
    session_factory, tmp_path, role, content_type
):
    asset_id, user_id, _ = await seed(session_factory)
    owner = await read(session_factory, asset_id)
    config = settings(tmp_path)
    config.idempotency_hours = 24
    app = app_for(session_factory, config, user_id, PresignStore())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/assets/upload-url",
            headers={"Idempotency-Key": f"upload-{role}"},
            json={
                "project_id": owner.project_id,
                "product_id": None,
                "filename": "reference.bin",
                "content_type": content_type,
                "size_bytes": 10,
                "role": role,
            },
        )
    assert response.status_code == 201, response.text
    assert response.json()["upload"]["fields"]["Content-Type"] == content_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "content_type"),
    [
        ("SOURCE_VIDEO", "image/png"),
        ("REFERENCE_VIDEO", "audio/wav"),
        ("PRODUCT_IMAGE", "video/mp4"),
        ("REFERENCE_AUDIO", "image/png"),
        ("BACKGROUND_AUDIO", "video/mp4"),
        ("PROJECT_REFERENCE", "audio/wav"),
        ("UNKNOWN_ROLE", "image/png"),
        ("PROJECT_REFERENCE", "application/octet-stream"),
    ],
)
async def test_upload_http_rejects_role_or_mime_mismatch_without_creating_asset(
    session_factory, tmp_path, role, content_type
):
    asset_id, user_id, _ = await seed(session_factory)
    owner = await read(session_factory, asset_id)
    config = settings(tmp_path)
    config.idempotency_hours = 24
    app = app_for(session_factory, config, user_id, PresignStore())
    async with session_factory() as session:
        before = await session.scalar(select(func.count()).select_from(Asset))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/assets/upload-url",
            headers={"Idempotency-Key": f"invalid-{role}-{content_type}"},
            json={
                "project_id": owner.project_id,
                "product_id": None,
                "filename": "invalid.bin",
                "content_type": content_type,
                "size_bytes": 10,
                "role": role,
            },
        )
    assert response.status_code == 422, response.text
    async with session_factory() as session:
        after = await session.scalar(select(func.count()).select_from(Asset))
    assert after == before


@pytest.mark.asyncio
async def test_http_202_durable_duplicate_conflict_and_policy(session_factory, tmp_path):
    asset_id, user_id, _ = await seed(session_factory)
    config = settings(tmp_path)
    app = app_for(session_factory, config, user_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        policy = await client.get("/assets/upload-policy")
        assert policy.status_code == 200
        assert policy.json() == {
            "max_upload_bytes": config.max_upload_bytes,
            "allowed_content_types": sorted(ALLOWED_TYPES),
        }
        for _ in range(2):
            response = await client.post(
                f"/assets/{asset_id}/complete", json={"checksum_sha256": "a" * 64}
            )
            assert response.status_code == 202
            assert response.json()["status"] == "VALIDATING"
            assert response.json()["media_metadata"]["validation"]["phase"] == "QUEUED"
        row = await read(session_factory, asset_id)
        assert row.media_metadata["validation"]["attempts"] == 0
        conflict = await client.post(
            f"/assets/{asset_id}/complete", json={"checksum_sha256": "b" * 64}
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "ASSET_CHECKSUM_CONFLICT"


@pytest.mark.asyncio
async def test_http_only_creator_can_complete(session_factory, tmp_path):
    asset_id, _, _ = await seed(session_factory)
    app = app_for(session_factory, settings(tmp_path), "other-user")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/assets/{asset_id}/complete", json={})
    assert response.status_code == 403
    assert (await read(session_factory, asset_id)).status == "PENDING_UPLOAD"


@pytest.mark.asyncio
async def test_http_ready_replay_and_failed_retry(session_factory, tmp_path):
    asset_id, user_id, _ = await seed(session_factory)
    config = settings(tmp_path)
    app = app_for(session_factory, config, user_id)
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id)
        row.status = "FAILED"
        row.media_metadata = {"validation": {"phase": "FAILED", "expected_checksum": None}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/assets/{asset_id}/complete", json={})
        assert response.status_code == 409 and response.json()["code"] == "ASSET_RETRY_REQUIRED"
        response = await client.post(
            f"/assets/{asset_id}/complete", json={"retry_validation": True}
        )
        assert response.status_code == 202
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id)
            row.status = "READY"
            row.checksum = "a" * 64
        response = await client.post(
            f"/assets/{asset_id}/complete", json={"checksum_sha256": "a" * 64}
        )
        assert response.status_code == 202 and response.json()["status"] == "READY"


@pytest.mark.asyncio
async def test_accepted_response_is_sent_only_after_queue_commit(session_factory, tmp_path):
    asset_id, user_id, _ = await seed(session_factory)
    app = app_for(session_factory, settings(tmp_path), user_id)
    sent = []

    async def monitored(scope, receive, send):
        async def capture(message):
            if message["type"] == "http.response.start":
                sent.append(message["status"])
                if message["status"] == 202:
                    # Independent transaction observes publication at send time,
                    # not after ASGITransport waits for dependency cleanup.
                    assert (await read(session_factory, asset_id)).status == "VALIDATING"
            await send(message)

        await app(scope, receive, capture)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=monitored), base_url="http://test"
    ) as client:
        response = await client.post(f"/assets/{asset_id}/complete", json={})
    assert response.status_code == 202 and sent == [202]


@pytest.mark.asyncio
async def test_commit_failure_cannot_report_accepted(session_factory, tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    asset_id, user_id, _ = await seed(session_factory)
    app = app_for(session_factory, settings(tmp_path), user_id)

    async def fail_commit(self):
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(AsyncSession, "commit", fail_commit)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.post(f"/assets/{asset_id}/complete", json={})
    assert response.status_code == 500
    assert (await read(session_factory, asset_id)).status == "PENDING_UPLOAD"
