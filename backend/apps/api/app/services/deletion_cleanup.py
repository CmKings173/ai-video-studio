"""Bounded cleanup orchestration for logically deleted resources.

This service only marks proven generated outputs for the existing asset
retention worker. It never deletes Asset rows or calls object storage directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import and_, case, exists, literal, or_, select, union
from sqlalchemy.exc import SQLAlchemyError

from apps.api.app.db.models import (
    Asset,
    DeletionOperation,
    DirectorRun,
    DirectorRunMember,
    FinalVideo,
    GenerationAsset,
    Scene,
    SceneGeneration,
    Video,
    utcnow,
)
from apps.api.app.services.asset_service import (
    asset_is_proven_generated_output,
    asset_live_reference_exists,
    has_durable_validation,
)
from apps.api.app.services.deletion_state import is_logically_deleted

logger = logging.getLogger(__name__)

_ELIGIBLE_OPERATION_STATUSES = frozenset(
    {"LOGICALLY_DELETED", "CLEANUP_FAILED", "CLEANUP_BLOCKED"}
)
_ACTIVE_WORK_STATUSES = frozenset(
    {
        "CREATED",
        "DISPATCHING",
        "QUEUED",
        "RUNNING",
        "COLLECTING",
        "ASSEMBLING",
        "CANCEL_REQUESTED",
    }
)
_PENDING_ASSET_STATUSES = frozenset({"PENDING", "PENDING_UPLOAD", "VALIDATING"})
_OUTPUT_ROLES = frozenset({"GENERATED_VIDEO", "FINAL_VIDEO"})


@dataclass(frozen=True)
class _OperationClaim:
    operation_id: str
    cleanup_started_at: datetime
    retry_count: int


@dataclass(frozen=True)
class _OperationOutcome:
    status: str
    error_code: str | None = None
    message: str | None = None


@dataclass
class DeletionCleanupReport:
    completed: list[str] = field(default_factory=list)
    pending: list[dict[str, str]] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)


class DeletionCleanupService:
    """Advance deletion operations and hand eligible output bytes to retention."""

    def __init__(self, factory, settings):
        self.factory = factory
        configured_batch = int(getattr(settings, "deletion_cleanup_batch_size", 50))
        self.batch_size = max(1, min(configured_batch, 100))
        retention_batch = int(getattr(settings, "retention_batch_size", 200))
        self.asset_batch_size = max(1, min(retention_batch, 200))
        self.retry_seconds = max(
            30, int(getattr(settings, "deletion_cleanup_retry_seconds", 300))
        )
        self.claim_timeout_seconds = max(
            60, int(getattr(settings, "asset_operation_claim_timeout_seconds", 900))
        )

    def _retry_delay(self, retry_count: int) -> timedelta:
        multiplier = 2 ** min(max(retry_count - 1, 0), 4)
        return timedelta(seconds=min(3600, self.retry_seconds * multiplier))

    async def _claim_batch(self, now: datetime) -> list[_OperationClaim]:
        retry_due = or_(
            DeletionOperation.next_attempt_at.is_(None),
            DeletionOperation.next_attempt_at <= now,
        )
        stale_before = now - timedelta(seconds=self.claim_timeout_seconds)
        pending_retry_due = and_(
            DeletionOperation.status == "CLEANUP_PENDING",
            DeletionOperation.next_attempt_at.is_not(None),
            DeletionOperation.next_attempt_at <= now,
        )
        pending_claim_stale = and_(
            DeletionOperation.status == "CLEANUP_PENDING",
            DeletionOperation.next_attempt_at.is_(None),
            or_(
                DeletionOperation.cleanup_started_at.is_(None),
                DeletionOperation.cleanup_started_at <= stale_before,
            ),
        )
        eligible = or_(
            and_(DeletionOperation.status.in_(_ELIGIBLE_OPERATION_STATUSES), retry_due),
            pending_retry_due,
            pending_claim_stale,
        )

        claims: list[_OperationClaim] = []
        async with self.factory() as session, session.begin():
            rows = (
                await session.scalars(
                    select(DeletionOperation)
                    .where(eligible)
                    .order_by(DeletionOperation.requested_at, DeletionOperation.id)
                    .limit(self.batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for operation in rows:
                retry_count = int(operation.retry_count or 0)
                if operation.status != "LOGICALLY_DELETED":
                    retry_count += 1
                operation.retry_count = retry_count
                operation.status = "CLEANUP_PENDING"
                operation.cleanup_started_at = now
                operation.next_attempt_at = None
                claims.append(
                    _OperationClaim(operation.id, now, retry_count)
                )
        return claims

    async def _resource_is_valid(self, session, operation: DeletionOperation) -> str | None:
        video = await session.get(Video, operation.video_id)
        if video is None:
            return "VIDEO_NOT_FOUND"
        if video.created_by != operation.resource_owner_id:
            return "RESOURCE_OWNER_MISMATCH"

        if operation.resource_type == "VIDEO":
            if operation.resource_id != operation.video_id:
                return "RESOURCE_VIDEO_MISMATCH"
        elif operation.resource_type == "SCENE":
            resource_exists = await session.scalar(
                select(
                    exists().where(
                        Scene.id == operation.resource_id,
                        Scene.video_id == operation.video_id,
                    )
                )
            )
            if not resource_exists:
                return "SCENE_NOT_FOUND"
        elif operation.resource_type == "GENERATION":
            resource_exists = await session.scalar(
                select(
                    exists().where(
                        SceneGeneration.id == operation.resource_id,
                        SceneGeneration.video_id == operation.video_id,
                    )
                )
            )
            if not resource_exists:
                return "GENERATION_NOT_FOUND"
        elif operation.resource_type == "FINAL_VIDEO":
            resource_exists = await session.scalar(
                select(
                    exists().where(
                        FinalVideo.id == operation.resource_id,
                        FinalVideo.video_id == operation.video_id,
                    )
                )
            )
            if not resource_exists:
                return "FINAL_VIDEO_NOT_FOUND"
        else:
            return "UNSUPPORTED_RESOURCE_TYPE"

        if not await is_logically_deleted(
            session,
            operation.resource_type,
            operation.resource_id,
            operation.video_id,
        ):
            return "RESOURCE_NOT_HIDDEN"
        return None

    async def _has_active_work(self, session, operation: DeletionOperation) -> bool:
        active_generation = await session.scalar(
            select(
                exists().where(
                    SceneGeneration.video_id == operation.video_id,
                    SceneGeneration.status.in_(_ACTIVE_WORK_STATUSES),
                )
            )
        )
        if active_generation:
            return True

        active_director_run = await session.scalar(
            select(
                exists().where(
                    DirectorRun.video_id == operation.video_id,
                    DirectorRun.status.in_(_ACTIVE_WORK_STATUSES),
                )
            )
        )
        if active_director_run:
            return True

        active_director_member = await session.scalar(
            select(
                select(DirectorRunMember.id)
                .join(DirectorRun, DirectorRun.id == DirectorRunMember.director_run_id)
                .where(
                    DirectorRun.video_id == operation.video_id,
                    DirectorRunMember.status.in_({"CREATED", "RUNNING"}),
                )
                .exists()
            )
        )
        if active_director_member:
            return True

        final_video_filters = [
            FinalVideo.video_id == operation.video_id,
            FinalVideo.status.in_(_ACTIVE_WORK_STATUSES),
        ]
        if operation.resource_type == "FINAL_VIDEO":
            final_video_filters.append(FinalVideo.id == operation.resource_id)
        return bool(
            await session.scalar(
                select(exists().where(*final_video_filters))
            )
        )

    def _output_reference_query(self, operation: DeletionOperation):
        selects = []
        if operation.resource_type in {"VIDEO", "SCENE", "GENERATION"}:
            generation_filters = [SceneGeneration.video_id == operation.video_id]
            member_filters = [DirectorRun.video_id == operation.video_id]
            if operation.resource_type == "SCENE":
                generation_filters.append(SceneGeneration.scene_id == operation.resource_id)
                member_filters.append(DirectorRunMember.scene_id == operation.resource_id)
            elif operation.resource_type == "GENERATION":
                generation_filters.append(SceneGeneration.id == operation.resource_id)
                member_filters.append(
                    DirectorRunMember.scene_generation_id == operation.resource_id
                )
            selects.extend(
                [
                    select(
                        SceneGeneration.output_asset_id.label("asset_id"),
                        literal("GENERATED_VIDEO").label("expected_role"),
                    ).where(
                        *generation_filters,
                        SceneGeneration.output_asset_id.is_not(None),
                    ),
                    select(
                        GenerationAsset.asset_id.label("asset_id"),
                        literal("GENERATED_VIDEO").label("expected_role"),
                    )
                    .join(
                        SceneGeneration,
                        SceneGeneration.id == GenerationAsset.generation_id,
                    )
                    .where(
                        *generation_filters,
                        GenerationAsset.role.like("DIRECTOR!_%", escape="!"),
                    ),
                    select(
                        DirectorRunMember.output_asset_id.label("asset_id"),
                        literal("GENERATED_VIDEO").label("expected_role"),
                    )
                    .join(
                        DirectorRun,
                        DirectorRun.id == DirectorRunMember.director_run_id,
                    )
                    .join(
                        SceneGeneration,
                        and_(
                            SceneGeneration.id == DirectorRunMember.scene_generation_id,
                            SceneGeneration.scene_id == DirectorRunMember.scene_id,
                            SceneGeneration.video_id == DirectorRun.video_id,
                        ),
                    )
                    .where(
                        *member_filters,
                        DirectorRunMember.output_asset_id.is_not(None),
                    ),
                ]
            )

        if operation.resource_type in {"VIDEO", "FINAL_VIDEO"}:
            final_video_filters = [FinalVideo.video_id == operation.video_id]
            if operation.resource_type == "FINAL_VIDEO":
                final_video_filters.append(FinalVideo.id == operation.resource_id)
            selects.append(
                select(
                    FinalVideo.output_asset_id.label("asset_id"),
                    literal("FINAL_VIDEO").label("expected_role"),
                ).where(*final_video_filters, FinalVideo.output_asset_id.is_not(None))
            )
        return union(*selects).subquery()

    async def _output_assets(self, session, operation: DeletionOperation):
        references = self._output_reference_query(operation)
        video = await session.get(Video, operation.video_id)
        other_roles = references.alias("other_output_roles")
        role_conflict = exists(
            select(1).where(
                other_roles.c.asset_id == references.c.asset_id,
                other_roles.c.expected_role != Asset.role,
            )
        )
        # This is only a scheduling hint. Every safety condition is rechecked
        # after locking; unsafe outputs remain visible and prevent completion.
        ready_to_delete = and_(
            Asset.status == "READY",
            asset_is_proven_generated_output(references.c.asset_id),
            Asset.resource_owner_id == operation.resource_owner_id,
            Asset.project_id == video.project_id,
            Asset.role == references.c.expected_role,
            ~role_conflict,
            Asset.operation_claim_id.is_(None),
            ~asset_live_reference_exists(references.c.asset_id),
        )
        rows = (
            await session.execute(
                select(references.c.asset_id, references.c.expected_role)
                .select_from(
                    references.join(Asset, Asset.id == references.c.asset_id)
                )
                .where(Asset.purged_at.is_(None))
                .order_by(
                    case((ready_to_delete, 0), else_=1),
                    case((asset_live_reference_exists(references.c.asset_id), 1), else_=0),
                    case((Asset.status == "READY", 0), else_=1),
                    references.c.asset_id,
                    references.c.expected_role,
                )
                .limit(self.asset_batch_size + 1)
                .with_for_update(of=Asset, skip_locked=True)
            )
        ).all()
        has_more = len(rows) > self.asset_batch_size
        rows = rows[: self.asset_batch_size]
        expected_roles: dict[str, set[str]] = {}
        for asset_id, role in rows:
            expected_roles.setdefault(asset_id, set()).add(role)
        # A skipped lock or missing row must prevent completion even when it
        # falls outside the bounded, lockable batch. EXISTS returns one boolean.
        unresolved = bool(await session.scalar(select(
            select(1)
            .select_from(references.outerjoin(Asset, Asset.id == references.c.asset_id))
            .where(
                Asset.purged_at.is_(None),
                references.c.asset_id.not_in(expected_roles),
            )
            .exists()
        )))
        has_more = has_more or unresolved
        if not expected_roles:
            return {}, has_more, (
                "OUTPUT_ASSET_MISSING_OR_LOCKED" if unresolved else None
            )

        # A batch boundary must not hide another canonical role for a selected
        # asset. The union has only two possible roles, so this stays bounded.
        for asset_id, role in (
            await session.execute(
                select(references.c.asset_id, references.c.expected_role)
                .where(references.c.asset_id.in_(expected_roles))
                .limit(2 * self.asset_batch_size)
            )
        ).all():
            expected_roles[asset_id].add(role)

        assets = (
            await session.scalars(
                select(Asset)
                .where(Asset.id.in_(expected_roles))
                .order_by(Asset.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        by_id = {asset.id: asset for asset in assets}
        missing_ids = set(expected_roles) - set(by_id)
        if missing_ids:
            return by_id, has_more, "OUTPUT_ASSET_MISSING_OR_LOCKED"
        return (by_id, has_more, expected_roles)

    def _set_outcome(
        self,
        operation: DeletionOperation,
        claim: _OperationClaim,
        outcome: _OperationOutcome,
        now: datetime,
    ) -> None:
        if outcome.status == "CLEANUP_COMPLETE":
            operation.status = "CLEANUP_COMPLETE"
            operation.cleanup_completed_at = now
            operation.next_attempt_at = None
            operation.last_error_code = None
            operation.last_error_message = None
            operation.last_error_at = None
            return

        operation.status = outcome.status
        operation.cleanup_completed_at = None
        operation.next_attempt_at = now + self._retry_delay(claim.retry_count)
        operation.last_error_code = outcome.error_code
        operation.last_error_message = outcome.message
        operation.last_error_at = now

    async def _process(self, claim: _OperationClaim, now: datetime) -> _OperationOutcome | None:
        async with self.factory() as session, session.begin():
            operation = await session.get(
                DeletionOperation, claim.operation_id, with_for_update=True
            )
            if (
                operation is None
                or operation.status != "CLEANUP_PENDING"
                or operation.cleanup_started_at != claim.cleanup_started_at
            ):
                return None

            error_code = await self._resource_is_valid(session, operation)
            if error_code:
                outcome = _OperationOutcome(
                    "CLEANUP_BLOCKED", error_code, "Resource identity or visibility is unresolved."
                )
            elif await self._has_active_work(session, operation):
                outcome = _OperationOutcome(
                    "CLEANUP_BLOCKED",
                    "ACTIVE_DEPENDENCY",
                    "Generation, Director, or assembly work is still active.",
                )
            else:
                video = await session.get(Video, operation.video_id)
                assets, has_more, roles_or_error = await self._output_assets(session, operation)
                if isinstance(roles_or_error, str):
                    outcome = _OperationOutcome(
                        "CLEANUP_BLOCKED",
                        roles_or_error,
                        "A canonical output Asset is missing or currently locked.",
                    )
                else:
                    expected_roles = roles_or_error or {}
                    blocked_code: str | None = None
                    waiting_for_retention = has_more
                    for asset_id, asset in assets.items():
                        if expected_roles.get(asset_id) != {asset.role}:
                            blocked_code = "OUTPUT_ROLE_MISMATCH"
                            continue
                        proven = await session.scalar(
                            select(asset_is_proven_generated_output(asset.id)).where(
                                Asset.id == asset.id
                            )
                        )
                        if (
                            not proven
                            or asset.kind != "VIDEO"
                            or asset.ownership_provenance != "VIDEO_PARENT"
                            or asset.resource_owner_id != operation.resource_owner_id
                            or asset.project_id != video.project_id
                        ):
                            blocked_code = "OUTPUT_PROVENANCE_UNRESOLVED"
                            continue
                        if asset.operation_claim_id is not None:
                            blocked_code = "ASSET_OPERATION_CLAIMED"
                            continue
                        if await session.scalar(
                            select(asset_live_reference_exists(asset.id))
                        ):
                            blocked_code = "LIVE_REFERENCE_REMAINS"
                            continue

                        if asset.status == "READY":
                            asset.status = "DELETED"
                            asset.deleted_at = asset.deleted_at or now
                            waiting_for_retention = True
                        elif asset.status == "DELETED" and asset.purged_at is not None:
                            continue
                        elif asset.status == "FAILED" and asset.failed_at is None:
                            blocked_code = "FAILED_ASSET_TIMESTAMP_MISSING"
                            continue
                        elif asset.status in _PENDING_ASSET_STATUSES and has_durable_validation(
                            asset
                        ):
                            blocked_code = "ASSET_VALIDATION_ACTIVE"
                            continue
                        elif asset.status in _PENDING_ASSET_STATUSES | {
                            "FAILED",
                            "DELETED",
                            "DELETING",
                        }:
                            waiting_for_retention = True
                        else:
                            blocked_code = "ASSET_STATE_UNRESOLVED"
                            continue

                    if blocked_code:
                        outcome = _OperationOutcome(
                            "CLEANUP_BLOCKED",
                            blocked_code,
                            "An output is claimed, referenced, or fails provenance checks.",
                        )
                    elif waiting_for_retention:
                        outcome = _OperationOutcome(
                            "CLEANUP_PENDING",
                            "RETENTION_PENDING",
                            "Eligible outputs are waiting for bounded Asset retention cleanup.",
                        )
                    else:
                        outcome = _OperationOutcome("CLEANUP_COMPLETE")

            self._set_outcome(operation, claim, outcome, now)
            return outcome

    async def _record_failure(
        self, claim: _OperationClaim, now: datetime
    ) -> bool:
        async with self.factory() as session, session.begin():
            operation = await session.get(
                DeletionOperation, claim.operation_id, with_for_update=True
            )
            if (
                operation is None
                or operation.status != "CLEANUP_PENDING"
                or operation.cleanup_started_at != claim.cleanup_started_at
            ):
                return False
            self._set_outcome(
                operation,
                claim,
                _OperationOutcome(
                    "CLEANUP_FAILED",
                    "CLEANUP_TRANSIENT_FAILURE",
                    "Cleanup could not complete; the operation will be retried.",
                ),
                now,
            )
            return True

    async def run(self, *, now: datetime | None = None) -> DeletionCleanupReport:
        now = now or utcnow()
        report = DeletionCleanupReport()
        try:
            claims = await self._claim_batch(now)
        except SQLAlchemyError as exc:
            logger.warning(
                "deletion_cleanup_claim_failed",
                extra={"error_type": type(exc).__name__},
            )
            report.failed.append(
                {"operation_id": "", "error_code": "CLEANUP_CLAIM_UNAVAILABLE"}
            )
            return report

        for claim in claims:
            try:
                outcome = await self._process(claim, now)
            except SQLAlchemyError as exc:
                logger.warning(
                    "deletion_cleanup_operation_failed",
                    extra={
                        "operation_id": claim.operation_id,
                        "error_type": type(exc).__name__,
                    },
                )
                try:
                    recorded = await self._record_failure(claim, now)
                except SQLAlchemyError:
                    recorded = False
                if recorded:
                    report.failed.append(
                        {
                            "operation_id": claim.operation_id,
                            "error_code": "CLEANUP_TRANSIENT_FAILURE",
                        }
                    )
                continue
            except Exception as exc:
                logger.warning(
                    "deletion_cleanup_operation_failed",
                    extra={
                        "operation_id": claim.operation_id,
                        "error_type": type(exc).__name__,
                    },
                )
                try:
                    recorded = await self._record_failure(claim, now)
                except SQLAlchemyError:
                    recorded = False
                if recorded:
                    report.failed.append(
                        {
                            "operation_id": claim.operation_id,
                            "error_code": "CLEANUP_TRANSIENT_FAILURE",
                        }
                    )
                continue

            if outcome is None:
                continue
            if outcome.status == "CLEANUP_COMPLETE":
                report.completed.append(claim.operation_id)
            elif outcome.status == "CLEANUP_PENDING":
                report.pending.append(
                    {"operation_id": claim.operation_id, "error_code": outcome.error_code or ""}
                )
            else:
                report.blocked.append(
                    {"operation_id": claim.operation_id, "error_code": outcome.error_code or ""}
                )
        return report
