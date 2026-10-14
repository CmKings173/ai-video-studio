"""Preserve Video creation actors independently of business ownership."""

from alembic import op
import sqlalchemy as sa


revision = "c8f3b5d02e74"
down_revision = "b7e2a4c91d63"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No historical attribution can be inferred reliably from the business owner.
    with op.batch_alter_table("videos") as batch_op:
        batch_op.add_column(sa.Column("created_actor_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_videos_created_actor_id_users",
            "users",
            ["created_actor_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("videos") as batch_op:
        batch_op.drop_constraint("fk_videos_created_actor_id_users", type_="foreignkey")
        batch_op.drop_column("created_actor_id")
