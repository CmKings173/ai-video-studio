import json
import os
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "f3a7c9e1d2b4"
REVISION = "a4c8e0f2b6d1"


def config(path):
    cfg = Config(ROOT / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def insert(db, identity, profile, *, scope=None, enabled=1):
    columns = "id,code,mode,quality_profile,version,workflow,slots,required_slots,profile,"
    columns += "workflow_hash,slot_map_hash,enabled,created_by,created_at,updated_at"
    values = [
        identity,
        identity,
        "t2v",
        "STANDARD",
        "1",
        "{}",
        "{}",
        "[]",
        json.dumps(profile),
        "a",
        "b",
        enabled,
        "u",
        "2026-10-07",
        "2026-10-07",
    ]
    if scope is not None:
        columns += ",execution_scope"
        values.append(scope)
    db.execute(
        f"INSERT INTO workflow_registry ({columns}) VALUES ({','.join('?' for _ in values)})",
        values,
    )


def legacy_db(tmp_path):
    path = tmp_path / "scope.db"
    cfg = config(path)
    command.upgrade(cfg, PREVIOUS)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO users (id,email,name,password_hash,role,is_active,created_at,updated_at) "
            "VALUES ('u','scope@test','test','hash','ADMIN',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        insert(db, "single", {})
        insert(db, "aggregate", {"export_mode": "segments"}, enabled=0)
    return path, cfg


def test_scope_migration_backfill_index_constraints_and_safe_roundtrip(tmp_path):
    path, cfg = legacy_db(tmp_path)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert dict(db.execute("SELECT id,execution_scope FROM workflow_registry")) == {
            "single": "single_scene",
            "aggregate": "aggregate",
        }
        db.execute("UPDATE workflow_registry SET enabled=1 WHERE id='aggregate'")
        for identity, scope in (
            ("duplicate-single", "single_scene"),
            ("duplicate-aggregate", "aggregate"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                insert(db, identity, {}, scope=scope)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            insert(db, "invalid", {}, scope="unknown", enabled=0)
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            db.execute("UPDATE workflow_registry SET execution_scope=NULL WHERE id='single'")
    with pytest.raises(ValueError, match="simultaneous enabled"):
        command.downgrade(cfg, PREVIOUS)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM workflow_registry WHERE enabled").fetchone()[0] == 2
        db.execute("UPDATE workflow_registry SET enabled=0 WHERE id='aggregate'")
    command.downgrade(cfg, PREVIOUS)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        assert dict(db.execute("SELECT id,execution_scope FROM workflow_registry")) == {
            "single": "single_scene",
            "aggregate": "aggregate",
        }


def test_scope_migration_refuses_loss_of_explicit_disabled_identity(tmp_path):
    path, cfg = legacy_db(tmp_path)
    command.upgrade(cfg, REVISION)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE workflow_registry SET execution_scope='single_scene' WHERE id='aggregate'"
        )
    with pytest.raises(ValueError, match="differs from legacy"):
        command.downgrade(cfg, PREVIOUS)


@pytest.mark.postgres
def test_postgres_scope_migration_backfill_uniqueness_and_downgrade_safety():
    """Exercise the migration itself inside a disposable isolated PostgreSQL schema."""
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set POSTGRES_TEST_DATABASE_URL for PostgreSQL migration verification")
    engine = sa.create_engine(sa.engine.make_url(url).set(drivername="postgresql+psycopg"))
    schema = f"scope_test_{uuid4().hex}"
    migration = importlib.import_module("migrations.versions.a4c8e0f2b6d1_workflow_execution_scope")
    try:
        with engine.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            db.execute(sa.text(f'SET LOCAL search_path TO "{schema}"'))
            db.execute(
                sa.text(
                    "CREATE TABLE workflow_registry (id text PRIMARY KEY, mode text NOT NULL, "
                    "quality_profile text NOT NULL, profile jsonb NOT NULL, "
                    "enabled boolean NOT NULL)"
                )
            )
            db.execute(
                sa.text(
                    "CREATE UNIQUE INDEX uq_workflow_registry_enabled_mode_profile "
                    "ON workflow_registry(mode,quality_profile) WHERE enabled"
                )
            )
            db.execute(
                sa.text(
                    "INSERT INTO workflow_registry VALUES ('s','t2v','BASE','{}',true),"
                    "('a','t2v','BASE','{\"export_mode\":\"segments\"}',false)"
                )
            )
            with Operations.context(MigrationContext.configure(db)):
                migration.upgrade()
                assert dict(
                    db.execute(sa.text("SELECT id,execution_scope FROM workflow_registry")).all()
                ) == {"s": "single_scene", "a": "aggregate"}
                db.execute(sa.text("UPDATE workflow_registry SET enabled=true WHERE id='a'"))
                for scope in ("single_scene", "aggregate"):
                    with pytest.raises(sa.exc.IntegrityError):
                        with db.begin_nested():
                            db.execute(
                                sa.text(
                                    "INSERT INTO workflow_registry VALUES "
                                    "(:id,'t2v','BASE','{}',true,:scope)"
                                ),
                                {"id": scope, "scope": scope},
                            )
                with pytest.raises(ValueError, match="simultaneous enabled"):
                    migration.downgrade()
                db.execute(sa.text("UPDATE workflow_registry SET enabled=false WHERE id='a'"))
                migration.downgrade()
                migration.upgrade()
                assert (
                    db.scalar(sa.text("SELECT execution_scope FROM workflow_registry WHERE id='a'"))
                    == "aggregate"
                )
    finally:
        with engine.begin() as db:
            db.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()
