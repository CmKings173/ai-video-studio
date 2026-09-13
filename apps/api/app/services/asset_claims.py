"""Small, row-locked ownership protocol for long-running asset operations."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import Asset, utcnow

REPAIR_CLAIM = "REPAIR"
OUTPUT_WRITE_CLAIM = "OUTPUT_WRITE"
CLAIM_TYPES = frozenset({REPAIR_CLAIM, OUTPUT_WRITE_CLAIM})


def claim_is_stale(asset: Asset, *, now: datetime, timeout_seconds: int) -> bool:
    claimed_at = asset.operation_claimed_at
    if asset.operation_claim_id is None or asset.operation_claim_type is None:
        return True
    if claimed_at is None:
        return True
    return claimed_at <= now - timedelta(seconds=timeout_seconds)


def clear_claim(asset: Asset) -> None:
    asset.operation_claim_id = None
    asset.operation_claim_type = None
    asset.operation_claimed_at = None


def owns_claim(asset: Asset, claim_id: str, claim_type: str) -> bool:
    return (
        asset.operation_claim_id == claim_id
        and asset.operation_claim_type == claim_type
    )


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
