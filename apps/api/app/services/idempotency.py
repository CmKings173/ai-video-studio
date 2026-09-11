from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import IdempotencyKey, utcnow


def payload_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def claim(
    session: AsyncSession,
    *,
    user_id: str,
    operation: str,
    key: str,
    payload: Any,
    hours: int = 24,
) -> tuple[IdempotencyKey, dict[str, Any] | None]:
    if not key or len(key) > 200:
        raise AppError("IDEMPOTENCY_KEY_REQUIRED", "A valid Idempotency-Key is required", 400)
    digest = payload_hash(payload)
    query = (
        select(IdempotencyKey)
        .where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.operation == operation,
            IdempotencyKey.key == key,
        )
        .with_for_update()
    )
    existing = await session.scalar(query)
    if existing is not None:
        if existing.expires_at <= utcnow():
            existing.request_hash = digest
            existing.response = {}
            existing.status_code = 102
            existing.expires_at = utcnow() + timedelta(hours=hours)
            return existing, None
        if existing.request_hash != digest:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used with a different request",
                409,
            )
        if existing.status_code == 102:
            raise AppError(
                "IDEMPOTENCY_IN_PROGRESS", "The original request is still processing", 409
            )
        return existing, dict(existing.response)
    record = IdempotencyKey(
        user_id=user_id,
        operation=operation,
        key=key,
        request_hash=digest,
        response={},
        status_code=102,
        expires_at=utcnow() + timedelta(hours=hours),
    )
    try:
        async with session.begin_nested():
            session.add(record)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(query)
        if existing is None:
            raise
        if existing.request_hash != digest:
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used with a different request",
                409,
            ) from None
        if existing.status_code == 102:
            raise AppError(
                "IDEMPOTENCY_IN_PROGRESS",
                "The original request is still processing",
                409,
            ) from None
        return existing, dict(existing.response)
    return record, None


def complete(record: IdempotencyKey, response: dict[str, Any], status_code: int) -> None:
    record.response = response
    record.status_code = status_code
