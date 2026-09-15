"""Small, row-locked ownership protocol for long-running asset operations."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import Asset, utcnow

logger = logging.getLogger(__name__)

REPAIR_CLAIM = "REPAIR"
OUTPUT_WRITE_CLAIM = "OUTPUT_WRITE"
CLAIM_TYPES = frozenset({REPAIR_CLAIM, OUTPUT_WRITE_CLAIM})
HEARTBEAT_CLAIM_LOST = "CLAIM_LOST"
HEARTBEAT_DB_FAILURE = "HEARTBEAT_DB_FAILURE"


class ClaimLossEvent(asyncio.Event):
    """Event raised when claim ownership is no longer safe to assume."""

    def __init__(self) -> None:
        super().__init__()
        self.reason: str | None = None
        self.error: BaseException | None = None

    def mark_lost(self, reason: str, error: BaseException | None = None) -> None:
        self.reason = reason
        self.error = error
        self.set()


def claim_is_stale(asset: Asset, *, now: datetime, timeout_seconds: int) -> bool:
    claimed_at = asset.operation_claimed_at
    if asset.operation_claim_id is None or asset.operation_claim_type is None:
        return True
    if claimed_at is None:
        return True
    return claimed_at <= now - timedelta(seconds=timeout_seconds)


def claim_is_active(asset: Asset, *, now: datetime, timeout_seconds: int) -> bool:
    return bool(asset.operation_claim_id) and not claim_is_stale(
        asset, now=now, timeout_seconds=timeout_seconds
    )


def claim_available_expression(model, *, now: datetime, timeout_seconds: int):
    cutoff = now - timedelta(seconds=timeout_seconds)
    return or_(
        model.operation_claim_id.is_(None),
        model.operation_claimed_at.is_(None),
        model.operation_claimed_at < cutoff,
    )


def clear_claim(asset: Asset) -> None:
    asset.operation_claim_id = None
    asset.operation_claim_type = None
    asset.operation_claimed_at = None


def owns_claim(asset: Asset, claim_id: str, claim_type: str) -> bool:
    return (
        asset.operation_claim_id == claim_id
        and asset.operation_claim_type == claim_type
    )


def mutation_allowed(
    asset: Asset,
    *,
    now: datetime,
    timeout_seconds: int,
    claim_id: str | None = None,
    claim_type: str | None = None,
) -> bool:
    """Allow mutation when unclaimed/stale, or by the exact current owner."""
    if claim_id and claim_type and owns_claim(asset, claim_id, claim_type):
        return True
    return not claim_is_active(asset, now=now, timeout_seconds=timeout_seconds)


def deletion_lifecycle_owns_object(asset: Asset, object_key: str) -> bool:
    """Return true when retention owns this asset's canonical object lifecycle."""
    if asset.object_key != object_key or asset.operation_claim_id:
        return False
    if asset.status == "DELETING":
        return True
    return asset.status == "DELETED" and asset.purged_at is not None


async def requeue_deletion_retry(
    factory,
    asset_id: str,
    object_key: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Make a deletion-owned asset durably retryable via retention cleanup.

    ``DELETING`` and physically purged ``DELETED`` rows are fenced away from
    OUTPUT_WRITE/REPAIR writers. Reopening a purged terminal row before a risky
    compensation delete means a transient object-store failure cannot leave
    ``DELETED + purged_at`` pointing at a recreated canonical object forever.
    """
    now = now or utcnow()
    async with factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        if not asset or asset.object_key != object_key:
            return False
        if asset.status == "DELETING":
            asset.purged_at = None
            asset.delete_claimed_at = now
            clear_claim(asset)
            return True
        if asset.status == "DELETED" and asset.purged_at is not None:
            asset.status = "DELETING"
            asset.purged_at = None
            asset.delete_claimed_at = now
            clear_claim(asset)
            return True
        return False


async def renew_claim(
    session: AsyncSession,
    asset_id: str,
    claim_id: str,
    claim_type: str,
    *,
    allowed_statuses: set[str] | frozenset[str],
    now: datetime | None = None,
) -> bool:
    """Renew only the exact owner while the lifecycle state remains compatible."""
    asset = await session.get(Asset, asset_id, with_for_update=True)
    if not asset or asset.status not in allowed_statuses or not owns_claim(
        asset, claim_id, claim_type
    ):
        return False
    asset.operation_claimed_at = now or utcnow()
    return True


async def release_claim(
    session: AsyncSession, asset_id: str, claim_id: str, claim_type: str
) -> bool:
    asset = await session.get(Asset, asset_id, with_for_update=True)
    if not asset or not owns_claim(asset, claim_id, claim_type):
        return False
    clear_claim(asset)
    return True


@asynccontextmanager
async def claim_heartbeat(
    factory,
    asset_id: str,
    claim_id: str,
    claim_type: str,
    *,
    timeout_seconds: int | float,
    allowed_statuses: set[str] | frozenset[str],
):
    """Renew a claim in short independent transactions during external I/O."""
    stopped = asyncio.Event()
    lost = ClaimLossEvent()
    interval = max(0.01, timeout_seconds / 3)

    async def beat() -> None:
        while True:
            try:
                await asyncio.wait_for(stopped.wait(), timeout=interval)
                return
            except TimeoutError:
                try:
                    async with factory() as session, session.begin():
                        renewed = await renew_claim(
                            session,
                            asset_id,
                            claim_id,
                            claim_type,
                            allowed_statuses=allowed_statuses,
                        )
                except SQLAlchemyError as exc:
                    lost.mark_lost(HEARTBEAT_DB_FAILURE, exc)
                    logger.warning(
                        "asset_claim_heartbeat_db_failure",
                        extra={
                            "asset_id": asset_id,
                            "claim_id": claim_id,
                            "claim_type": claim_type,
                            "error_type": type(exc).__name__,
                        },
                    )
                    return
                if not renewed:
                    lost.mark_lost(HEARTBEAT_CLAIM_LOST)
                    return

    task = asyncio.create_task(beat())
    try:
        yield lost
    finally:
        stopped.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def acquire_claim(
    session: AsyncSession,
    asset_id: str,
    claim_type: str,
    *,
    timeout_seconds: int,
    allowed_statuses: set[str] | frozenset[str],
    now: datetime | None = None,
) -> str | None:
    """Acquire or take over one asset claim inside the caller's transaction.

    The caller must commit the transaction before external I/O. The row lock makes
    acquisition atomic across worker processes; stale claims are replaced only
    while holding that same lock.
    """
    if claim_type not in CLAIM_TYPES:
        raise ValueError(f"Unsupported asset claim type: {claim_type}")
    now = now or utcnow()
    asset = await session.get(Asset, asset_id, with_for_update=True)
    if asset is None or asset.status not in allowed_statuses:
        return None
    if asset.operation_claim_id and not claim_is_stale(
        asset, now=now, timeout_seconds=timeout_seconds
    ):
        return None
    claim_id = str(uuid4())
    asset.operation_claim_id = claim_id
    asset.operation_claim_type = claim_type
    asset.operation_claimed_at = now
    return claim_id
