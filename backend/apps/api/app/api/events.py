from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.core.metrics import metrics
from apps.api.app.core.security import hash_token
from apps.api.app.db.models import (
    FinalVideo,
    Scene,
    SceneGeneration,
    User,
    Video,
    utcnow,
)
from apps.api.app.db.models import (
    Session as AuthSession,
)
from apps.api.app.db.session import get_session_factory

router = APIRouter(tags=["events"])


def _generation_event(status: str) -> str:
    return {
        "CREATED": "generation.queued",
        "DISPATCHING": "generation.queued",
        "QUEUED": "generation.queued",
        "RUNNING": "generation.started",
        "COLLECTING": "generation.progress",
        "COMPLETED": "generation.completed",
        "FAILED": "generation.failed",
        "CANCEL_REQUESTED": "generation.progress",
        "CANCELLED": "generation.cancelled",
    }.get(status, "generation.progress")


def _assembly_event(status: str) -> str:
    return {
        "QUEUED": "assembly.progress",
        "ASSEMBLING": "assembly.started",
        "READY": "assembly.completed",
        "FAILED": "assembly.failed",
        "CANCEL_REQUESTED": "assembly.progress",
        "CANCELLED": "assembly.failed",
    }.get(status, "assembly.progress")


async def _snapshot(factory, video_id: str) -> list[tuple[str, int, str, dict[str, Any]]]:
    async with factory() as session:
        video = await session.get(Video, video_id)
        if video is None:
            return []
        result: list[tuple[str, int, str, dict[str, Any]]] = [
            (
                f"video:{video.id}",
                video.revision,
                "video.updated",
                {
                    "video_id": video.id,
                    "status": video.status,
                    "current_final_video_id": video.current_final_video_id,
                },
            )
        ]
        scenes = list(
            (
                await session.scalars(
                    select(Scene)
                    .where(Scene.video_id == video_id)
                    .order_by(Scene.scene_order, Scene.id)
                )
            ).all()
        )
        result.extend(
            (
                f"scene:{scene.id}",
                scene.revision,
                "scene.updated",
                {
                    "video_id": video_id,
                    "scene_id": scene.id,
                    "scene_order": scene.scene_order,
                    "enabled": scene.enabled,
                    "selected_generation_id": scene.selected_generation_id,
                },
            )
            for scene in scenes
        )
        generations = list(
            (
                await session.scalars(
                    select(SceneGeneration)
                    .where(SceneGeneration.video_id == video_id)
                    .order_by(SceneGeneration.created_at, SceneGeneration.id)
                )
            ).all()
        )
        result.extend(
            (
                f"generation:{generation.id}",
                generation.revision,
                _generation_event(generation.status),
                {
                    "video_id": video_id,
                    "scene_id": generation.scene_id,
                    "generation_id": generation.id,
                    "status": generation.status,
                    "stage": generation.phase,
                    "progress": (
                        round(generation.progress_current / generation.progress_total * 100)
                        if generation.progress_total
                        else None
                    ),
                    "current": generation.progress_current,
                    "total": generation.progress_total,
                    "error_code": generation.error_code,
                },
            )
            for generation in generations
        )
        finals = list(
            (
                await session.scalars(
                    select(FinalVideo)
                    .where(FinalVideo.video_id == video_id)
                    .order_by(FinalVideo.created_at, FinalVideo.id)
                )
            ).all()
        )
        result.extend(
            (
                f"final:{final.id}",
                final.revision,
                _assembly_event(final.status),
                {
                    "video_id": video_id,
                    "final_video_id": final.id,
                    "version_no": final.version_no,
                    "status": final.status,
                    "stage": final.phase,
                    "progress": (
                        round(final.progress_current / final.progress_total * 100)
                        if final.progress_total
                        else None
                    ),
                    "current": final.progress_current,
                    "total": final.progress_total,
                    "error_code": final.error_code,
                },
            )
            for final in finals
        )
        return result


def _encode(event_id: int, event_type: str, revision: int, data: dict[str, Any]) -> str:
    payload = {
        "event_id": str(event_id),
        "schema_version": 1,
        "resource_revision": revision,
        "type": event_type,
        "occurred_at": utcnow().isoformat(),
        **data,
    }
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}\n\n"
    )


def _dedupe_token(key: str, revision: int, data: dict[str, Any]) -> object:
    if key.startswith("video:"):
        return (revision, data.get("status"), data.get("current_final_video_id"))
    if key.startswith("final:"):
        return (revision, data.get("status"), data.get("error_code"))
    return revision


async def _stream(
    request: Request, factory, video_id: str, settings: Settings
) -> AsyncIterator[str]:
    revisions: dict[str, object] = {}
    event_id = 0
    heartbeat_at = asyncio.get_running_loop().time()
    while not await request.is_disconnected():
        if not await _stream_user_is_active(request, factory, settings):
            return
        snapshot = await _snapshot(factory, video_id)
        for key, revision, event_type, data in snapshot:
            # Worker lifecycle writes intentionally do not bump the optimistic
            # concurrency revision. Include status-bearing fields in the SSE
            # dedupe token so clients still observe terminal/video transitions.
            token = _dedupe_token(key, revision, data)
            if revisions.get(key) == token:
                continue
            revisions[key] = token
            event_id += 1
            yield _encode(event_id, event_type, revision, data)
        now = asyncio.get_running_loop().time()
        if now - heartbeat_at >= settings.sse_heartbeat_seconds:
            heartbeat_at = now
            yield f": heartbeat {utcnow().isoformat()}\n\n"
        await asyncio.sleep(settings.sse_poll_seconds)


async def _counted_stream(
    request: Request, factory, video_id: str, settings: Settings
) -> AsyncIterator[str]:
    metrics.inc("studio_sse_connections_active")
    try:
        async for event in _stream(request, factory, video_id, settings):
            yield event
    finally:
        metrics.inc("studio_sse_connections_active", -1)


async def _stream_user_is_active(request: Request, factory, settings: Settings) -> bool:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return False
    async with factory() as session:
        auth_session = await session.get(AuthSession, hash_token(token))
        if auth_session is None or auth_session.expires_at <= utcnow():
            return False
        user = await session.get(User, auth_session.user_id)
        return user is not None and user.is_active and user.role in {"ADMIN", "EDITOR"}


@router.get("/videos/{video_id}/events")
async def video_events(
    video_id: str,
    request: Request,
    factory=Depends(get_session_factory),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AppError("AUTH_REQUIRED", "Authentication required", 401)
    async with factory() as auth_session:
        session_row = await auth_session.get(AuthSession, hash_token(token))
        user = await auth_session.get(User, session_row.user_id) if session_row else None
        if session_row is None or session_row.expires_at <= utcnow():
            raise AppError("SESSION_EXPIRED", "Session expired", 401)
        if user is None or not user.is_active:
            raise AppError("ACCOUNT_DISABLED", "Account is disabled", 403)
        if user.role not in {"ADMIN", "EDITOR"}:
            raise AppError("FORBIDDEN", "Editor access required", 403)
    async with factory() as session:
        if await session.get(Video, video_id) is None:
            raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
    return StreamingResponse(
        _counted_stream(request, factory, video_id, settings),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
