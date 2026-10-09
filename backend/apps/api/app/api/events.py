from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select

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

LIVE_EVENT_PAGE_SIZE = 100
STREAM_AUTH_REVALIDATE_SECONDS = 20
ACTIVE_GENERATION_STATUSES = (
    "CREATED",
    "DISPATCHING",
    "QUEUED",
    "RUNNING",
    "COLLECTING",
    "CANCEL_REQUESTED",
)
ACTIVE_FINAL_STATUSES = ("QUEUED", "ASSEMBLING", "CANCEL_REQUESTED")

EventCursor = tuple[datetime, str]
EventRow = tuple[str, int, str, dict[str, Any]]


@dataclass(frozen=True)
class EventSnapshot:
    events: list[EventRow]
    generation_cursor: EventCursor
    final_cursor: EventCursor
    new_generation_ids: set[str]
    new_final_ids: set[str]
    active_generation_ids: set[str]
    active_final_ids: set[str]


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


def _after_cursor(model, cursor: EventCursor):
    created_at, item_id = cursor
    return or_(
        model.created_at > created_at,
        and_(model.created_at == created_at, model.id > item_id),
    )


async def _snapshot(
    factory,
    video_id: str,
    *,
    generation_cursor: EventCursor,
    final_cursor: EventCursor,
    active_generation_ids: set[str],
    active_final_ids: set[str],
) -> EventSnapshot:
    async with factory() as session:
        video = await session.get(Video, video_id)
        if video is None:
            return EventSnapshot(
                events=[],
                generation_cursor=generation_cursor,
                final_cursor=final_cursor,
                new_generation_ids=set(),
                new_final_ids=set(),
                active_generation_ids=set(),
                active_final_ids=set(),
            )
        result: list[EventRow] = [
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
        generation_live_filter = [SceneGeneration.status.in_(ACTIVE_GENERATION_STATUSES)]
        if active_generation_ids:
            generation_live_filter.append(SceneGeneration.id.in_(active_generation_ids))
        generations = list(
            (
                await session.scalars(
                    select(SceneGeneration)
                    .where(
                        SceneGeneration.video_id == video_id,
                        or_(*generation_live_filter),
                    )
                )
            ).all()
        )
        new_generations = list(
            (
                await session.scalars(
                    select(SceneGeneration)
                    .where(
                        SceneGeneration.video_id == video_id,
                        _after_cursor(SceneGeneration, generation_cursor),
                    )
                    .order_by(SceneGeneration.created_at, SceneGeneration.id)
                    .limit(LIVE_EVENT_PAGE_SIZE)
                )
            ).all()
        )
        new_generation_ids = {generation.id for generation in new_generations}
        generation_cursor = (
            (new_generations[-1].created_at, new_generations[-1].id)
            if new_generations
            else generation_cursor
        )
        generation_by_id = {generation.id: generation for generation in generations}
        generation_by_id.update({generation.id: generation for generation in new_generations})
        generations = list(generation_by_id.values())
        current_active_generation_ids = {
            generation.id
            for generation in generations
            if generation.status in ACTIVE_GENERATION_STATUSES
        }
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
                    "output_asset_id": generation.output_asset_id,
                },
            )
            for generation in generations
        )
        final_live_filter = [FinalVideo.status.in_(ACTIVE_FINAL_STATUSES)]
        if active_final_ids:
            final_live_filter.append(FinalVideo.id.in_(active_final_ids))
        finals = list(
            (
                await session.scalars(
                    select(FinalVideo)
                    .where(FinalVideo.video_id == video_id, or_(*final_live_filter))
                )
            ).all()
        )
        new_finals = list(
            (
                await session.scalars(
                    select(FinalVideo)
                    .where(
                        FinalVideo.video_id == video_id,
                        _after_cursor(FinalVideo, final_cursor),
                    )
                    .order_by(FinalVideo.created_at, FinalVideo.id)
                    .limit(LIVE_EVENT_PAGE_SIZE)
                )
            ).all()
        )
        new_final_ids = {final.id for final in new_finals}
        final_cursor = (
            (new_finals[-1].created_at, new_finals[-1].id)
            if new_finals
            else final_cursor
        )
        final_by_id = {final.id: final for final in finals}
        final_by_id.update({final.id: final for final in new_finals})
        finals = list(final_by_id.values())
        current_active_final_ids = {
            final.id for final in finals if final.status in ACTIVE_FINAL_STATUSES
        }
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
                    "output_asset_id": final.output_asset_id,
                },
            )
            for final in finals
        )
        return EventSnapshot(
            events=result,
            generation_cursor=generation_cursor,
            final_cursor=final_cursor,
            new_generation_ids=new_generation_ids,
            new_final_ids=new_final_ids,
            active_generation_ids=current_active_generation_ids,
            active_final_ids=current_active_final_ids,
        )


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
    fields = (
        "status",
        "stage",
        "progress",
        "current",
        "total",
        "error_code",
        "output_asset_id",
        "current_final_video_id",
        "enabled",
        "selected_generation_id",
    )
    return (revision, *(data.get(field) for field in fields))


async def _stream(
    request: Request, factory, video_id: str, settings: Settings
) -> AsyncIterator[str]:
    state_tokens: dict[str, object] = {}
    active_generation_tokens: dict[str, object] = {}
    active_final_tokens: dict[str, object] = {}
    generation_cursor: EventCursor = (utcnow(), "")
    final_cursor: EventCursor = generation_cursor
    event_id = 0
    heartbeat_at = asyncio.get_running_loop().time()
    next_auth_check = 0.0
    while not await request.is_disconnected():
        now = asyncio.get_running_loop().time()
        if now >= next_auth_check:
            if not await _stream_user_is_active(request, factory, settings):
                return
            next_auth_check = now + STREAM_AUTH_REVALIDATE_SECONDS
        snapshot = await _snapshot(
            factory,
            video_id,
            generation_cursor=generation_cursor,
            final_cursor=final_cursor,
            active_generation_ids=set(active_generation_tokens),
            active_final_ids=set(active_final_tokens),
        )
        generation_cursor = snapshot.generation_cursor
        final_cursor = snapshot.final_cursor
        next_state_tokens: dict[str, object] = {}
        for key, revision, event_type, data in snapshot.events:
            token = _dedupe_token(key, revision, data)
            if key.startswith("generation:"):
                generation_id = key.removeprefix("generation:")
                previous = active_generation_tokens.get(generation_id)
                if data["status"] in ACTIVE_GENERATION_STATUSES:
                    active_generation_tokens[generation_id] = token
                    if previous == token:
                        continue
                else:
                    active_generation_tokens.pop(generation_id, None)
                    if previous == token or (
                        previous is None and generation_id not in snapshot.new_generation_ids
                    ):
                        continue
            elif key.startswith("final:"):
                final_id = key.removeprefix("final:")
                previous = active_final_tokens.get(final_id)
                if data["status"] in ACTIVE_FINAL_STATUSES:
                    active_final_tokens[final_id] = token
                    if previous == token:
                        continue
                else:
                    active_final_tokens.pop(final_id, None)
                    if previous == token or (
                        previous is None and final_id not in snapshot.new_final_ids
                    ):
                        continue
            else:
                next_state_tokens[key] = token
                if state_tokens.get(key) == token:
                    continue
            event_id += 1
            yield _encode(event_id, event_type, revision, data)
        state_tokens = next_state_tokens
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
