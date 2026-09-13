from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from alembic import command
from alembic.config import Config

from apps.api.app.services.retention import RetentionPolicy

ROOT = Path(__file__).resolve().parents[2]


def test_migration_backfills_failed_at_and_supports_cycles(tmp_path):
    db_file = tmp_path / "migration_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    cfg = Config(ROOT / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    # 1. Upgrade to the historical retention revision before the new claim schema
    command.upgrade(cfg, "c0f2a7e9d1b3")

    # 2. Seed a FAILED asset with known updated_at (older than retention window)
    known_time = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users ("
        "id, email, name, password_hash, role, is_active, "
        "created_at, updated_at"
        ") VALUES ("
        "'user-1', 'mig@test.local', 'Admin', 'hash', "
        "'ADMIN', 1, ?, ?)",
        (known_time.isoformat(), known_time.isoformat()),
    )
    cur.execute(
        "INSERT INTO assets ("
        "id, kind, role, filename, content_type, object_key, "
        "status, size_bytes, created_by, created_at, updated_at"
        ") VALUES ("
        "'asset-failed-1', 'VIDEO', 'GENERATED_VIDEO', "
        "'fail.mp4', 'video/mp4', 'outputs/fail.mp4', "
        "'FAILED', 100, 'user-1', ?, ?)",
        (known_time.isoformat(), known_time.isoformat()),
    )
    conn.commit()
    conn.close()

    # 3. Upgrade from historical c0 to the new head
    command.upgrade(cfg, "head")

    # 4. Assert failed_at was backfilled with prior updated_at
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute(
        "SELECT status, failed_at, purged_at, "
        "delete_claimed_at, updated_at "
        "FROM assets WHERE id = 'asset-failed-1'"
    )
    row = cur.fetchone()


    assert row is not None
    assert row[0] == "FAILED"
    # In SQLite, DateTime(timezone=True) is stored as ISO string
    assert row[1] is not None
    assert "2026-01-15" in row[1]
    # purged_at and delete_claimed_at are NULL
    assert row[2] is None
    assert row[3] is None

    columns = {item[1] for item in cur.execute("PRAGMA table_info(assets)").fetchall()}
    assert {
        "failed_at",
        "purged_at",
        "delete_claimed_at",
        "operation_claim_id",
        "operation_claim_type",
        "operation_claimed_at",
    } <= columns
    index_names = {item[1] for item in cur.execute("PRAGMA index_list(assets)").fetchall()}
    assert "ix_assets_retention_deleted" in index_names
    assert "ix_assets_retention_deleting" in index_names
    # Verify retention eligibility via mock asset object
    policy = RetentionPolicy(pending_hours=24, failed_hours=24, deleted_hours=168)
    now = datetime(2026, 1, 17, 12, 0, 0, tzinfo=UTC)
    failed_dt = datetime.fromisoformat(row[1]) if isinstance(row[1], str) else row[1]
    if failed_dt.tzinfo is None:
        failed_dt = failed_dt.replace(tzinfo=UTC)
    assert failed_dt < now - timedelta(hours=24)
    mock_asset = SimpleNamespace(status="FAILED", failed_at=failed_dt, purged_at=None)
    assert policy.eligible(mock_asset, now=now) is True

    conn.close()

    # 5. Verify downgrade cycle
    command.downgrade(cfg, "c0f2a7e9d1b3")

    # 6. Verify re-upgrade cycle
    command.upgrade(cfg, "head")
