"""add explicit failed timestamp and deletion claim state"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0f2a7e9d1b3"
down_revision: str | None = "b4f6a8c0d2e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = "status IN ('PENDING','PENDING_UPLOAD','VALIDATING','READY','FAILED','DELETING','DELETED')"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets", recreate="always") as batch:
            batch.add_column(sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
            batch.drop_constraint("status", type_="check")
            batch.create_check_constraint("status", _STATUS)
    else:
        op.add_column("assets", sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        # ``op.f`` preserves the convention-resolved name created by the
        # initial schema migration; passing the name directly would apply the
        # convention a second time (``ck_assets_ck_assets_status``).
        op.drop_constraint(op.f("ck_assets_status"), "assets", type_="check")
        op.create_check_constraint(op.f("ck_assets_status"), "assets", _STATUS)
    op.create_index("ix_assets_retention_failed", "assets", ["status", "failed_at", "id"])
    op.create_index("ix_assets_retention_deleted", "assets", ["status", "deleted_at", "id"])
    op.create_index("ix_assets_retention_deleting", "assets", ["status", "updated_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_assets_retention_deleting", table_name="assets")
    op.drop_index("ix_assets_retention_deleted", table_name="assets")
    op.drop_index("ix_assets_retention_failed", table_name="assets")
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets", recreate="always") as batch:
            batch.drop_column("failed_at")
            batch.drop_constraint("status", type_="check")
            batch.create_check_constraint(
                "status",
                "status IN ('PENDING','PENDING_UPLOAD','VALIDATING','READY','FAILED','DELETED')",
            )
    else:
        op.drop_constraint(op.f("ck_assets_status"), "assets", type_="check")
        op.create_check_constraint(
            op.f("ck_assets_status"),
            "assets",
            "status IN ('PENDING','PENDING_UPLOAD','VALIDATING','READY','FAILED','DELETED')",
        )
        op.drop_column("assets", "failed_at")
