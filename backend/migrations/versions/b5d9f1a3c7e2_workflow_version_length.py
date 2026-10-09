"""Allow the complete version identity of Director aggregate workflows."""

import sqlalchemy as sa
from alembic import op

revision = "b5d9f1a3c7e2"
down_revision = "a4c8e0f2b6d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("workflow_registry") as batch:
        batch.alter_column(
            "version", existing_type=sa.String(64), type_=sa.String(128), existing_nullable=False
        )


def downgrade():
    # Refuse narrowing rather than truncating identities or breaking (code, version).
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM workflow_registry WHERE length(version) > 64")
    ):
        raise ValueError("Cannot downgrade while workflow version identities exceed 64 characters")
    with op.batch_alter_table("workflow_registry") as batch:
        batch.alter_column(
            "version", existing_type=sa.String(128), type_=sa.String(64), existing_nullable=False
        )
