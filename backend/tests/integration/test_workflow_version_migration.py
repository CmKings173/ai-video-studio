import importlib
import os
import sqlite3
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.integration.test_workflow_execution_scope_migration import legacy_db
from tests.integration.test_workflow_version_length import LONG_VERSION

MODULE = "migrations.versions.b5d9f1a3c7e2_workflow_version_length"


def test_version_migration_preserves_rows_and_refuses_lossy_downgrade(tmp_path):
    path, cfg = legacy_db(tmp_path)
    command.upgrade(cfg, "b5d9f1a3c7e2")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM workflow_registry").fetchone()[0] == 2
        db.execute("UPDATE workflow_registry SET version=? WHERE id='aggregate'", (LONG_VERSION,))
        assert (
            "VARCHAR(128)"
            in db.execute(
                "SELECT sql FROM sqlite_master WHERE name='workflow_registry'"
            ).fetchone()[0]
        )
    with pytest.raises(ValueError, match="exceed 64"):
        command.downgrade(cfg, "a4c8e0f2b6d1")
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT version FROM workflow_registry WHERE id='aggregate'").fetchone()[0]
            == LONG_VERSION
        )
        db.execute("UPDATE workflow_registry SET version='1'")
    command.downgrade(cfg, "a4c8e0f2b6d1")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM workflow_registry").fetchone()[0] == 2


@pytest.mark.postgres
def test_postgres_version_migration_preserves_table_and_unique_identity():
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires a disposable PostgreSQL test database")
    engine = sa.create_engine(sa.engine.make_url(url).set(drivername="postgresql+psycopg"))
    migration = importlib.import_module(MODULE)
    schema = f"version_test_{uuid4().hex}"
    try:
        with engine.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            db.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            db.execute(
                sa.text(
                    "CREATE TABLE workflow_registry (code text,version varchar(64) NOT NULL,"
                    "UNIQUE(code,version))"
                )
            )
            db.execute(sa.text("INSERT INTO workflow_registry VALUES ('test','1')"))
            oid = db.scalar(sa.text("SELECT 'workflow_registry'::regclass::oid"))
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
                assert db.scalar(sa.text("SELECT 'workflow_registry'::regclass::oid")) == oid
                db.execute(
                    sa.text("INSERT INTO workflow_registry VALUES ('test',:version)"),
                    {"version": LONG_VERSION},
                )
                with pytest.raises(sa.exc.IntegrityError), db.begin_nested():
                    db.execute(
                        sa.text("INSERT INTO workflow_registry VALUES ('test',:version)"),
                        {"version": LONG_VERSION},
                    )
                with pytest.raises(ValueError, match="exceed 64"):
                    migration.downgrade()
                db.execute(
                    sa.text("DELETE FROM workflow_registry WHERE version=:version"),
                    {"version": LONG_VERSION},
                )
                migration.downgrade()
                assert db.scalar(sa.text("SELECT count(*) FROM workflow_registry")) == 1
    finally:
        with engine.begin() as db:
            db.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
