"""Add the durable Phase 4 deletion-operation contract.

The operation keeps polymorphic resource identity and owning-video context as
audit values rather than foreign keys. Its row must remain after the resource
or video is physically removed. User attribution uses RESTRICT foreign keys.
No historical resources are backfilled.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "d4e6f8a0b2c4"
down_revision: str | None = "c8f3b5d02e74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RESOURCE_TYPE_CHECK = "resource_type IN ('VIDEO','SCENE','GENERATION','FINAL_VIDEO')"
_STATUS_CHECK = (
    "status IN ('DELETE_REQUESTED','WAITING_FOR_DEPENDENCIES','LOGICALLY_DELETED',"
    "'CLEANUP_PENDING','CLEANUP_COMPLETE','CLEANUP_FAILED','CLEANUP_BLOCKED')"
)
_LOGICAL_TIMESTAMP_CHECK = (
    "(status = 'DELETE_REQUESTED' AND logically_deleted_at IS NULL) OR "
    "(status <> 'DELETE_REQUESTED' AND logically_deleted_at IS NOT NULL)"
)


def upgrade() -> None:
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "deletion_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resource_type", sa.String(length=16), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        # Intentionally no video/resource FK: completed operations outlive the
        # rows they describe and must not block safe physical cleanup.
        sa.Column("video_id", sa.String(length=36), nullable=False),
        sa.Column("resource_owner_id", sa.String(length=36), nullable=False),
        sa.Column("requested_by_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("preview_snapshot", json_type, nullable=False),
        sa.Column("dependency_snapshot", json_type, nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("logically_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_message", sa.String(length=500), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_RESOURCE_TYPE_CHECK, name="ck_deletion_operations_resource_type"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_deletion_operations_status"),
        sa.CheckConstraint(
            "expected_revision >= 1",
            name="ck_deletion_operations_expected_revision_positive",
        ),
        sa.CheckConstraint(
            "retry_count >= 0",
            name="ck_deletion_operations_retry_count_nonnegative",
        ),
        sa.CheckConstraint(
            _LOGICAL_TIMESTAMP_CHECK,
            name="ck_deletion_operations_logical_timestamp_matches_status",
        ),
        sa.ForeignKeyConstraint(
            ["resource_owner_id"],
            ["users.id"],
            name="fk_deletion_operations_resource_owner_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_deletion_operations_requested_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deletion_operations"),
        sa.UniqueConstraint(
            "resource_type", "resource_id", name="uq_deletion_operations_resource"
        ),
    )
    op.create_index(
        "ix_deletion_operations_status_next_attempt",
        "deletion_operations",
        ["status", "next_attempt_at", "id"],
    )
    op.create_index(
        "ix_deletion_operations_video_status",
        "deletion_operations",
        ["video_id", "status"],
    )
    op.create_index(
        "ix_deletion_operations_owner_requested_at",
        "deletion_operations",
        ["resource_owner_id", "requested_at"],
    )
    op.create_index(
        "ix_deletion_operations_actor_requested_at",
        "deletion_operations",
        ["requested_by_id", "requested_at"],
    )


def downgrade() -> None:
    op.drop_table("deletion_operations")
