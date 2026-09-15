"""Claim-based asset retention shared by API and reconciliation workers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select

from apps.api.app.db.models import Asset, utcnow
from apps.api.app.integrations.minio import AssetStoreError
from apps.api.app.services.asset_claims import claim_available_expression, clear_claim
from apps.api.app.services.asset_service import asset_reference_exists
from apps.api.app.services.retention import RetentionPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetentionCandidate:
    asset_id: str
    object_key: str
    status: str


@dataclass(frozen=True)
class RetentionCleanupResult:
    candidates: list[dict[str, Any]]
    deleted_objects: int
    retained_records: int
    has_more: bool = False
    deleted_keys: tuple[str, ...] = ()


class AssetRetentionService:
    """Keep DB state authoritative while external object deletion is retriable."""

    def __init__(self, factory, store, settings):
        self.factory = factory
        self.store = store
        self.policy = RetentionPolicy.from_settings(settings)
        self.batch_size = int(getattr(settings, "retention_batch_size", 200))
        self.claim_timeout_seconds = int(
            getattr(settings, "asset_operation_claim_timeout_seconds", 900)
        )

    def _eligibility(self, now: datetime):
        pending_cutoff = now - timedelta(hours=self.policy.pending_hours)
        failed_cutoff = now - timedelta(hours=self.policy.failed_hours)
        deleted_cutoff = now - timedelta(hours=self.policy.deleted_hours)
        deleting_cutoff = now - timedelta(hours=self.policy.deleting_retry_hours)
        eligible = or_(
            and_(
                Asset.status.in_({"PENDING", "PENDING_UPLOAD", "VALIDATING"}),
                Asset.created_at < pending_cutoff,
            ),
            and_(
                Asset.status == "FAILED",
                Asset.failed_at.is_not(None),
                Asset.failed_at < failed_cutoff,
            ),
            and_(
                Asset.status == "DELETED",
                Asset.deleted_at.is_not(None),
                Asset.deleted_at < deleted_cutoff,
                Asset.purged_at.is_(None),
            ),
            and_(
                Asset.status == "DELETING",
                func.coalesce(Asset.delete_claimed_at, Asset.updated_at) < deleting_cutoff,
            ),
        )
        claim_available = claim_available_expression(
            Asset, now=now, timeout_seconds=self.claim_timeout_seconds
        )
        return and_(eligible, claim_available)

    @staticmethod
    def _order_timestamp():
        return case(
            (Asset.status == "FAILED", Asset.failed_at),
            (Asset.status == "DELETED", Asset.deleted_at),
            (
                Asset.status == "DELETING",
                func.coalesce(Asset.delete_claimed_at, Asset.updated_at),
            ),
            else_=Asset.created_at,
        )

    async def preview(self, *, now: datetime | None = None) -> RetentionCleanupResult:
        now = now or utcnow()
        referenced = asset_reference_exists(Asset.id).label("referenced")
        async with self.factory() as session:
            rows = (
                await session.execute(
                    select(Asset, referenced)
                    .where(self._eligibility(now))
                    .order_by(self._order_timestamp(), Asset.id)
                    .limit(self.batch_size + 1)
                )
            ).all()
        has_more = len(rows) > self.batch_size
        candidates = [
            {
                "asset_id": asset.id,
                "object_key": asset.object_key,
                "status": asset.status,
                "referenced": bool(is_referenced),
            }
            for asset, is_referenced in rows[: self.batch_size]
        ]
        return RetentionCleanupResult(candidates, 0, 0, has_more)

    async def claim_batch(self, *, now: datetime | None = None) -> list[RetentionCandidate]:
        """Atomically claim an eligible, unreferenced bounded batch."""
        now = now or utcnow()
        async with self.factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(Asset)
                    .where(self._eligibility(now), ~asset_reference_exists(Asset.id))
                    .order_by(self._order_timestamp(), Asset.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            if not rows:
                return []
            # Recheck references after row locks in one bounded SQL query. Attach
            # paths lock the same Asset row before inserting their reference.
            locked_ids = [asset.id for asset in rows]
            still_unreferenced = set(
                (
                    await session.scalars(
                        select(Asset.id).where(
                            Asset.id.in_(locked_ids), ~asset_reference_exists(Asset.id)
                        )
                    )
                ).all()
            )
            claims: list[RetentionCandidate] = []
            for asset in rows:
                if asset.id not in still_unreferenced:
                    continue
                clear_claim(asset)
                asset.status = "DELETING"
                asset.delete_claimed_at = now
                claims.append(RetentionCandidate(asset.id, asset.object_key, asset.status))
            return claims

    async def finalize(self, asset_id: str, *, now: datetime | None = None) -> bool:
        """Finalize only after this service has run the deletion protocol."""
        now = now or utcnow()
        async with self.factory() as session:
            asset = await session.get(Asset, asset_id)
            if asset is None or asset.status != "DELETING":
                return False
            object_key = asset.object_key
        try:
            await self.store.delete(object_key)
        except (AssetStoreError, OSError, TimeoutError, ConnectionError) as exc:
            logger.warning(
                "asset_retention_finalize_delete_unavailable",
                extra={
                    "asset_id": asset_id,
                    "object_key": object_key,
                    "error_type": type(exc).__name__,
                    "operation": "finalize_delete",
                },
            )
            return False
        async with self.factory() as session, session.begin():
            asset = await session.get(Asset, asset_id, with_for_update=True)
            if asset is None or asset.status != "DELETING" or asset.object_key != object_key:
                return False
            asset.status = "DELETED"
            asset.deleted_at = asset.deleted_at or now
            asset.purged_at = now
            asset.delete_claimed_at = None
            clear_claim(asset)
            return True

    async def cleanup(
        self, *, dry_run: bool = False, now: datetime | None = None
    ) -> RetentionCleanupResult:
        now = now or utcnow()
        if dry_run:
            return await self.preview(now=now)
        claims = await self.claim_batch(now=now)
        deleted_objects = 0
        failed = 0
        deleted_keys: list[str] = []
        candidates = [
            {
                "asset_id": item.asset_id,
                "object_key": item.object_key,
                "status": "DELETING",
                "referenced": False,
            }
            for item in claims
        ]
        for item in claims:
            try:
                await self.store.delete(item.object_key)
            except (AssetStoreError, OSError, TimeoutError, ConnectionError) as exc:
                failed += 1
                logger.warning(
                    "asset_retention_delete_unavailable",
                    extra={
                        "asset_id": item.asset_id,
                        "object_key": item.object_key,
                        "error_type": type(exc).__name__,
                        "operation": "delete",
                    },
                )
                continue
            if await self.finalize(item.asset_id, now=now):
                deleted_objects += 1
                deleted_keys.append(item.object_key)
            else:
                failed += 1
        has_more = False
        if len(claims) == self.batch_size:
            async with self.factory() as session:
                more_id = await session.scalar(
                    select(Asset.id)
                    .where(
                        self._eligibility(now),
                        ~asset_reference_exists(Asset.id),
                        ~Asset.id.in_([c.asset_id for c in claims]),
                    )
                    .limit(1)
                )
                has_more = more_id is not None
        return RetentionCleanupResult(
            candidates, deleted_objects, failed, has_more, tuple(deleted_keys)
        )
