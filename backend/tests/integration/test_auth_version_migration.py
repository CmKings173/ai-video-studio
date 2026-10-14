"""Real isolated migration checks; never connects to an application database."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_auth_version_migration_preserves_accounts_and_revokes_raw_updates(tmp_path):
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "account-revocation.db"
    config = Config(root / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(config, "d4e6f8a0b2c4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users (id,email,name,password_hash,role,is_active,created_at,updated_at) "
            "VALUES ('account','migration@example.test','Account','hash','ADMIN',1,"
            "'2026-10-12','2026-10-12')"
        )
    command.upgrade(config, "e5a7c9b1d3f6")
    with sqlite3.connect(database) as connection:
        def version():
            return connection.execute("SELECT auth_version FROM users").fetchone()[0]

        assert version() == 1
        for expected, statement in enumerate(
            [
                "UPDATE users SET role='EDITOR'",
                "UPDATE users SET is_active=0",
                "UPDATE users SET password_hash='new-hash'",
            ],
            start=2,
        ):
            connection.execute(statement)
            assert version() == expected
        connection.execute("UPDATE users SET auth_version=2")
        assert version() == 4
        # An ORM-style preincrement and the SQL trigger must not double-increment.
        connection.execute("UPDATE users SET role='ADMIN', auth_version=5")
        assert version() == 5
        connection.execute("UPDATE users SET name='Renamed'")
        assert version() == 5
    command.downgrade(config, "d4e6f8a0b2c4")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT name,password_hash FROM users").fetchone() == (
            "Renamed", "new-hash"
        )
