"""add asset operation ownership claims and purge markers"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e4f7a8c2b9"
down_revision: str | None = "c0f2a7e9d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUS = "status IN ('PENDING','PENDING_UPLOAD','VALIDATING','READY','FAILED','DELETING','DELETED')"
_CLAIM_TYPE = "operation_claim_type IS NULL OR operation_claim_type IN ('REPAIR','OUTPUT_WRITE')"


def upgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_assets_retention_deleted", table_name="assets")
    op.drop_index("ix_assets_retention_deleting", table_name="assets")
    columns = [
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_claim_id", sa.String(length=64), nullable=True),
        sa.Column("operation_claim_type", sa.String(length=32), nullable=True),
        sa.Column("operation_claimed_at", sa.DateTime(timezone=True), nullable=True),
    ]
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets", recreate="always") as batch:
            for column in columns:
                batch.add_column(column)
            batch.create_check_constraint("operation_claim_type", _CLAIM_TYPE)
    else:
        for column in columns:
            op.add_column("assets", column)
        op.create_check_constraint("ck_assets_operation_claim_type", "assets", _CLAIM_TYPE)
    op.execute(
        sa.text(
            "UPDATE assets SET failed_at = updated_at "
            "WHERE status = 'FAILED' AND failed_at IS NULL"
        )
    )
    op.create_index(
        "ix_assets_retention_deleted", "assets", ["status", "deleted_at", "purged_at", "id"]
    )
    op.create_index(
        "ix_assets_retention_deleting", "assets", ["status", "delete_claimed_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assets_retention_deleting", table_name="assets")
    op.drop_index("ix_assets_retention_deleted", table_name="assets")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets", recreate="always") as batch:
            batch.drop_constraint("operation_claim_type", type_="check")
            batch.drop_column("operation_claimed_at")
            batch.drop_column("operation_claim_type")
            batch.drop_column("operation_claim_id")
            batch.drop_column("delete_claimed_at")
            batch.drop_column("purged_at")
    else:
        op.drop_constraint("ck_assets_operation_claim_type", "assets", type_="check")
        for name in (
            "operation_claimed_at",
            "operation_claim_type",
            "operation_claim_id",
            "delete_claimed_at",
            "purged_at",
        ):
            op.drop_column("assets", name)
    op.create_index("ix_assets_retention_deleted", "assets", ["status", "deleted_at", "id"])
    op.create_index("ix_assets_retention_deleting", "assets", ["status", "updated_at", "id"])

