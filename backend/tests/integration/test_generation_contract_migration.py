import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config


def test_contract_migration_backfills_and_preserves_profile_enablement(tmp_path):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "contracts.db"
    cfg = Config(root / "alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "d1e4f7a8c2b9")
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO users (id,email,name,password_hash,role,is_active,created_at,updated_at) "
            "VALUES ('u','migration@test','test','hash','ADMIN',1,"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO workflow_registry (id,code,mode,version,workflow,slots,"
            "required_slots,profile,"
            "workflow_hash,slot_map_hash,enabled,created_by,created_at,updated_at) "
            "VALUES ('w','old','t2v','1','{}','{}','[]','{}','a','b',1,'u',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT quality_profile,enabled FROM workflow_registry WHERE id='w'"
        ).fetchone() == ("STANDARD", 1)
        assert "media_metadata" in {row[1] for row in db.execute("PRAGMA table_info(assets)")}
        assert "output_metadata" in {
            row[1] for row in db.execute("PRAGMA table_info(scene_generations)")
        }
        assert "generation_config" in {row[1] for row in db.execute("PRAGMA table_info(scenes)")}
        db.execute(
            "INSERT INTO workflow_registry (id,code,mode,quality_profile,version,workflow,slots,"
            "required_slots,profile,"
            "workflow_hash,slot_map_hash,enabled,created_by,created_at,updated_at) "
            "VALUES ('d','draft','t2v','DRAFT','1','{}','{}','[]','{}','a','b',1,'u',"
            "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        assert (
            db.execute("SELECT count(*) FROM workflow_registry WHERE enabled=1").fetchone()[0] == 2
        )
        db.execute("UPDATE workflow_registry SET mode='i2v_last' WHERE id='d'")
        db.execute("DELETE FROM workflow_registry WHERE id='d'")
    command.downgrade(cfg, "d1e4f7a8c2b9")
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT quality_profile,enabled FROM workflow_registry WHERE id='w'"
        ).fetchone() == ("STANDARD", 1)
