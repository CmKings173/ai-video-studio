"""persist generation contracts and independent workflow quality enablement"""

import sqlalchemy as sa
from alembic import op

revision = "e2b6c8d0f4a1"
down_revision = "d1e4f7a8c2b9"
branch_labels = None
depends_on = None

OLD_MODES = "mode IN ('t2v','i2v','i2v_first_last','r2v')"
NEW_MODES = "mode IN ('t2v','i2v','i2v_last','i2v_first_last','r2v')"


def change_mode(table, expression):
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_mode"), type_="check")
            batch.create_check_constraint(op.f(f"ck_{table}_mode"), expression)
    else:
        op.drop_constraint(op.f(f"ck_{table}_mode"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_mode"), table, expression)


def upgrade():
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    for table, column in (
        ("scenes", "generation_config"),
        ("scene_generations", "output_metadata"),
        ("assets", "media_metadata"),
    ):
        op.add_column(
            table, sa.Column(column, json_type, nullable=False, server_default=sa.text("'{}'"))
        )
    op.add_column(
        "workflow_registry",
        sa.Column("quality_profile", sa.String(16), nullable=False, server_default="STANDARD"),
    )
    op.drop_index("uq_workflow_registry_enabled_mode", table_name="workflow_registry")
    change_mode("scene_generations", NEW_MODES)
    change_mode("workflow_registry", NEW_MODES)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("workflow_registry") as batch:
            batch.create_check_constraint(
                "quality_profile", "quality_profile IN ('DRAFT','STANDARD','HIGH')"
            )
    else:
        op.create_check_constraint(
            op.f("ck_workflow_registry_quality_profile"),
            "workflow_registry",
            "quality_profile IN ('DRAFT','STANDARD','HIGH')",
        )
    op.create_index(
        "uq_workflow_registry_enabled_mode_profile",
        "workflow_registry",
        ["mode", "quality_profile"],
        unique=True,
        postgresql_where=sa.text("enabled"),
        sqlite_where=sa.text("enabled = 1"),
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM workflow_registry WHERE mode='i2v_last' OR quality_profile <> 'STANDARD'"
        )
    ) or bind.scalar(sa.text("SELECT count(*) FROM scene_generations WHERE mode='i2v_last'")):
        raise ValueError("Cannot downgrade while last-frame or non-Standard workflow records exist")
    op.drop_index("uq_workflow_registry_enabled_mode_profile", table_name="workflow_registry")
    change_mode("scene_generations", OLD_MODES)
    change_mode("workflow_registry", OLD_MODES)
    with op.batch_alter_table("workflow_registry") as batch:
        batch.drop_constraint(op.f("ck_workflow_registry_quality_profile"), type_="check")
        batch.drop_column("quality_profile")
    op.create_index(
        "uq_workflow_registry_enabled_mode",
        "workflow_registry",
        ["mode"],
        unique=True,
        postgresql_where=sa.text("enabled"),
        sqlite_where=sa.text("enabled = 1"),
    )
    for table, column in (
        ("scenes", "generation_config"),
        ("scene_generations", "output_metadata"),
        ("assets", "media_metadata"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
