"""Make workflow execution scope an independent registry identity."""

import sqlalchemy as sa
from alembic import op

revision = "a4c8e0f2b6d1"
down_revision = "f3a7c9e1d2b4"
branch_labels = None
depends_on = None

OLD_INDEX = "uq_workflow_registry_enabled_mode_profile"
NEW_INDEX = "uq_workflow_registry_enabled_mode_profile_scope"
CHECK = "ck_workflow_registry_execution_scope"


def legacy_scope_expression():
    export_mode = (
        "profile ->> 'export_mode'"
        if op.get_bind().dialect.name == "postgresql"
        else "json_extract(profile, '$.export_mode')"
    )
    return f"CASE WHEN {export_mode} = 'segments' THEN 'aggregate' ELSE 'single_scene' END"


def upgrade():
    op.add_column(
        "workflow_registry",
        sa.Column("execution_scope", sa.String(16), nullable=False, server_default="single_scene"),
    )
    op.execute(
        sa.text(f"UPDATE workflow_registry SET execution_scope = {legacy_scope_expression()}")
    )
    with op.batch_alter_table("workflow_registry") as batch:
        batch.create_check_constraint(
            op.f(CHECK), "execution_scope IN ('single_scene','aggregate')"
        )
    op.drop_index(OLD_INDEX, table_name="workflow_registry")
    op.create_index(
        NEW_INDEX,
        "workflow_registry",
        ["mode", "quality_profile", "execution_scope"],
        unique=True,
        postgresql_where=sa.text("enabled"),
        sqlite_where=sa.text("enabled = 1"),
    )


def downgrade():
    bind = op.get_bind()
    # Fail before any DDL. Old selection cannot represent simultaneous enabled variants.
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM (SELECT mode, quality_profile FROM workflow_registry "
            "WHERE enabled GROUP BY mode, quality_profile HAVING count(*) > 1) AS variants"
        )
    ):
        raise ValueError(
            "Cannot downgrade while simultaneous enabled execution scope variants exist"
        )
    # Even a disabled explicit identity must survive rollback through the legacy ingest rule.
    if bind.scalar(
        sa.text(
            f"SELECT count(*) FROM workflow_registry WHERE execution_scope <> {legacy_scope_expression()}"
        )
    ):
        raise ValueError(
            "Cannot downgrade while execution scope differs from legacy profile identity"
        )
    op.drop_index(NEW_INDEX, table_name="workflow_registry")
    with op.batch_alter_table("workflow_registry") as batch:
        batch.drop_constraint(op.f(CHECK), type_="check")
        batch.drop_column("execution_scope")
    op.create_index(
        OLD_INDEX,
        "workflow_registry",
        ["mode", "quality_profile"],
        unique=True,
        postgresql_where=sa.text("enabled"),
        sqlite_where=sa.text("enabled = 1"),
    )
