from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from apps.api.app.api import events
from apps.api.app.api.events import EventSnapshot
from apps.api.app.core.config import Settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.security import create_access_token
from apps.api.app.db.models import User, Video

JWT_SECRET = "sse-integration-test-signing-secret-at-least-32-bytes"
JWT_ISSUER = "ai-video-studio-tests"
JWT_AUDIENCE = "ai-video-studio-api-tests"


def sse_settings(**overrides: object) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "jwt_secret": JWT_SECRET,
        "jwt_issuer": JWT_ISSUER,
        "jwt_audience": JWT_AUDIENCE,
        "jwt_expiration_seconds": 3,
        "sse_poll_seconds": 0.1,
        "sse_heartbeat_seconds": 5,
    }
    values.update(overrides)
    return Settings(**values)


def request_with_cookie(token: str | None) -> Request:
    headers = [] if token is None else [(b"cookie", f"studio_session={token}".encode())]

    async def receive():
        await asyncio.sleep(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/videos/video-1/events",
            "raw_path": b"/api/v1/videos/video-1/events",
            "query_string": b"",
            "headers": headers,
            "server": ("test", 80),
            "client": ("127.0.0.1", 12345),
            "root_path": "",
        },
        receive,
    )


class VideoOnlySession:
    async def execute(self, statement):
        assert statement.column_descriptions[0]["entity"] is User
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(role="EDITOR", is_active=True, auth_version=1)
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, statement):
        assert statement.column_descriptions[0]["entity"] is Video
        return SimpleNamespace(
            id="video-1",
            created_by="user-123",
            revision=1,
            status="READY",
            current_final_video_id=None,
        )


def video_factory():
    return VideoOnlySession()


@pytest.mark.asyncio
async def test_sse_rejects_missing_or_invalid_jwt_before_opening_stream():
    settings = sse_settings()

    for token in (None, "not.a.jwt"):
        with pytest.raises(AppError) as error:
            await events.video_events(
                "video-1", request_with_cookie(token), factory=video_factory, settings=settings
            )
        assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_sse_rejects_expired_jwt_before_opening_stream():
    settings = sse_settings()
    token = jwt.encode(
        {
            "sub": "user-123",
            "role": "EDITOR",
            "auth_version": 1,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": 0,
            "exp": 1,
            "csrf": "csrf-test-token-value-that-is-long-enough",
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AppError) as error:
        await events.video_events(
            "video-1", request_with_cookie(token), factory=video_factory, settings=settings
        )

    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_jwt_opens_sse_after_loading_owned_video():
    token, _ = create_access_token("user-123", "EDITOR", sse_settings())

    response = await events.video_events(
        "video-1",
        request_with_cookie(token),
        factory=video_factory,
        settings=sse_settings(),
    )

    assert response.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_sse_stream_stops_at_jwt_expiration(monkeypatch):
    settings = sse_settings()
    token, _ = create_access_token("user-123", "EDITOR", settings)
    snapshots = 0

    async def snapshot(*_args, **_kwargs):
        nonlocal snapshots
        snapshots += 1
        rows = []
        if snapshots == 1:
            rows = [("video:video-1", 1, "video.updated", {"status": "READY"})]
        now = datetime.now(UTC)
        cursor = (now, "")
        return EventSnapshot(
            events=rows,
            generation_cursor=cursor,
            final_cursor=cursor,
            new_generation_ids=set(),
            new_final_ids=set(),
            active_generation_ids=set(),
            active_final_ids=set(),
        )

    monkeypatch.setattr(events, "_snapshot", snapshot)
    stream = events._stream(request_with_cookie(token), video_factory, "video-1", settings)

    first_event = await asyncio.wait_for(anext(stream), timeout=1)
    assert "event: video.updated" in first_event

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=5)
    assert snapshots >= 1


class BlockingVideoSession:
    async def execute(self, statement):
        assert statement.column_descriptions[0]["entity"] is User
        return SimpleNamespace(
            one_or_none=lambda: SimpleNamespace(role="EDITOR", is_active=True, auth_version=1)
        )

    def __init__(self, started: asyncio.Event, closed: asyncio.Event, *, block_snapshot: bool):
        self.started = started
        self.closed = closed
        self.block_snapshot = block_snapshot

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        if self.block_snapshot:
            self.closed.set()

    async def scalar(self, statement):
        assert statement.column_descriptions[0]["entity"] is Video
        if self.block_snapshot:
            self.started.set()
            await asyncio.Event().wait()
        return SimpleNamespace(
            id="video-1",
            created_by="user-123",
            revision=1,
            status="READY",
            current_final_video_id=None,
        )


def blocking_video_factory(started: asyncio.Event, closed: asyncio.Event):
    session_count = 0

    def factory():
        nonlocal session_count
        session_count += 1
        return BlockingVideoSession(started, closed, block_snapshot=session_count > 1)

    return factory


@pytest.mark.asyncio
async def test_blocked_snapshot_is_bounded_by_jwt_expiration_and_closes_session(monkeypatch):
    settings = sse_settings(jwt_expiration_seconds=30)
    token, _ = create_access_token("user-123", "EDITOR", settings)
    expires_at = jwt.decode(token, options={"verify_signature": False})["exp"]
    started, closed = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(events, "time", SimpleNamespace(time=lambda: expires_at - 0.05))
    stream = events._stream(
        request_with_cookie(token), blocking_video_factory(started, closed), "video-1", settings
    )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)

    assert started.is_set()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_blocked_snapshot_has_independent_timeout_and_closes_session(monkeypatch):
    settings = sse_settings(jwt_expiration_seconds=30)
    token, _ = create_access_token("user-123", "EDITOR", settings)
    started, closed = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(events, "STREAM_SNAPSHOT_TIMEOUT_SECONDS", 0.05)
    stream = events._stream(
        request_with_cookie(token), blocking_video_factory(started, closed), "video-1", settings
    )

    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)

    assert started.is_set()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_asgi_client_disconnect_cancels_blocked_snapshot_and_closes_session(monkeypatch):
    settings = sse_settings(jwt_expiration_seconds=30)
    token, _ = create_access_token("user-123", "EDITOR", settings)
    started, closed = asyncio.Event(), asyncio.Event()
    scope = request_with_cookie(token).scope
    request_available = True

    async def receive():
        nonlocal request_available
        if request_available:
            request_available = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await started.wait()
        return {"type": "http.disconnect"}

    request = Request(scope, receive)

    async def remain_connected():
        return False

    monkeypatch.setattr(request, "is_disconnected", remain_connected)
    response = StreamingResponse(
        events._counted_stream(
            request,
            blocking_video_factory(started, closed),
            "video-1",
            settings,
        ),
        media_type="text/event-stream",
    )
    messages = []

    async def send(message):
        messages.append(message)

    preexisting_tasks = asyncio.all_tasks()
    await asyncio.wait_for(response(scope, receive, send), timeout=1)

    assert any(message["type"] == "http.response.start" for message in messages)
    assert not any(
        message["type"] == "http.response.body" and message.get("body") for message in messages
    )
    assert closed.is_set()
    leaked_tasks = {
        task
        for task in asyncio.all_tasks()
        if task not in preexisting_tasks and task is not asyncio.current_task() and not task.done()
    }
    assert leaked_tasks == set()
