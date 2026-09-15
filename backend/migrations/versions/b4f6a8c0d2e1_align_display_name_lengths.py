"""align display name lengths with the public contract

Revision ID: b4f6a8c0d2e1
Revises: a1d9e2f4b7c8
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4f6a8c0d2e1"
down_revision: str | None = "a1d9e2f4b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("users", "projects", "brands", "products", "videos")


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            column = "title" if table == "videos" else "name"
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column(
                    column,
                    existing_type=sa.String(length=200),
                    type_=sa.String(length=255),
                    existing_nullable=False,
                )
    else:
        for table in TABLES:
            column = "title" if table == "videos" else "name"
            op.alter_column(
                table,
                column,
                existing_type=sa.String(length=200),
                type_=sa.String(length=255),
                existing_nullable=False,
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for table in reversed(TABLES):
            column = "title" if table == "videos" else "name"
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column(
                    column,
                    existing_type=sa.String(length=255),
                    type_=sa.String(length=200),
                    existing_nullable=False,
                )
    else:
        for table in reversed(TABLES):
            column = "title" if table == "videos" else "name"
            op.alter_column(
                table,
                column,
                existing_type=sa.String(length=255),
                type_=sa.String(length=200),
                existing_nullable=False,
            )
