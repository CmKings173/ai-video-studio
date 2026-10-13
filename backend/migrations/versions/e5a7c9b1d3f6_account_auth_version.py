"""Revoke account JWTs after role, activation or password changes.

Existing accounts start at version 1. Tokens without the version claim require
a fresh login. No sessions, resource ownership or historical outputs are changed.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5a7c9b1d3f6"
down_revision: str | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("auth_version", sa.Integer(), nullable=False, server_default="1"))
        batch.create_check_constraint("auth_version_positive", "auth_version >= 1")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION studio_revoke_account_tokens() RETURNS trigger AS $$
            BEGIN
                IF NEW.role IS DISTINCT FROM OLD.role
                   OR NEW.is_active IS DISTINCT FROM OLD.is_active
                   OR NEW.password_hash IS DISTINCT FROM OLD.password_hash THEN
                    NEW.auth_version := GREATEST(OLD.auth_version + 1, NEW.auth_version);
                ELSE
                    NEW.auth_version := GREATEST(OLD.auth_version, NEW.auth_version);
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER studio_revoke_account_tokens
            BEFORE UPDATE OF role, is_active, password_hash, auth_version ON users
            FOR EACH ROW EXECUTE FUNCTION studio_revoke_account_tokens()
        """)
    elif op.get_bind().dialect.name == "sqlite":
        op.execute("""
            CREATE TRIGGER studio_revoke_account_tokens
            AFTER UPDATE OF role, is_active, password_hash, auth_version ON users
            WHEN NEW.auth_version < OLD.auth_version
                 OR ((NEW.role IS NOT OLD.role OR NEW.is_active IS NOT OLD.is_active
                      OR NEW.password_hash IS NOT OLD.password_hash)
                     AND NEW.auth_version <= OLD.auth_version)
            BEGIN
                UPDATE users SET auth_version = OLD.auth_version +
                    CASE WHEN NEW.role IS NOT OLD.role OR NEW.is_active IS NOT OLD.is_active
                              OR NEW.password_hash IS NOT OLD.password_hash THEN 1 ELSE 0 END
                WHERE id = NEW.id;
            END
        """)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER studio_revoke_account_tokens ON users")
        op.execute("DROP FUNCTION studio_revoke_account_tokens()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER studio_revoke_account_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("auth_version_positive", type_="check")
        batch.drop_column("auth_version")
