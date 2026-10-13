"""Persist stable Asset business ownership and grant provenance."""

from alembic import op
import sqlalchemy as sa


revision = "b7e2a4c91d63"
down_revision = "a6d2e8f4b1c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical rows deliberately remain NULL. Their ownership must be
    # established separately instead of inferred from an actor's current role.
    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("resource_owner_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("ownership_provenance", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_assets_resource_owner_id_users",
            "users",
            ["resource_owner_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ownership_provenance",
            "ownership_provenance IS NULL OR ownership_provenance IN "
            "('OWNER','ADMIN_ASSIGNMENT','VIDEO_PARENT')",
        )
        batch_op.create_index("ix_assets_resource_owner_id", ["resource_owner_id"])


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_index("ix_assets_resource_owner_id")
        batch_op.drop_constraint("ownership_provenance", type_="check")
        batch_op.drop_constraint("fk_assets_resource_owner_id_users", type_="foreignkey")
        batch_op.drop_column("ownership_provenance")
        batch_op.drop_column("resource_owner_id")
