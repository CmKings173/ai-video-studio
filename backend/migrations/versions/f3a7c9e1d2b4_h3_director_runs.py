"""add MiniMax H3 Director modes and durable aggregate runs"""

import sqlalchemy as sa
from alembic import op


revision = "f3a7c9e1d2b4"
down_revision = "e2b6c8d0f4a1"
branch_labels = None
depends_on = None

OLD_MODES = "mode IN ('t2v','i2v','i2v_last','i2v_first_last','r2v')"
NEW_MODES = (
    "mode IN ('t2v','i2v','fl2v','i2v_last','i2v_first_last','r2v','v2v','rv2v')"
)


def change_mode(table: str, expression: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_mode"), type_="check")
            batch.create_check_constraint(op.f(f"ck_{table}_mode"), expression)
    else:
        op.drop_constraint(op.f(f"ck_{table}_mode"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_mode"), table, expression)


def upgrade() -> None:
    json_type = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")
    change_mode("scene_generations", NEW_MODES)
    change_mode("workflow_registry", NEW_MODES)
    with op.batch_alter_table("workflow_registry") as batch:
        batch.drop_constraint(op.f("ck_workflow_registry_quality_profile"), type_="check")
        batch.create_check_constraint(op.f("ck_workflow_registry_quality_profile"),
            "quality_profile IN ('DRAFT','STANDARD','HIGH','BASE','HD','FULL_HD_REFINED','CUSTOM')")

    op.create_table(
        "director_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="RESTRICT"), nullable=False),
        sa.Column(
            "workflow_id",
            sa.String(36),
            sa.ForeignKey("workflow_registry.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False, server_default="minimax_h3_director"),
        sa.Column("task", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="CREATED"),
        sa.Column("phase", sa.String(64), nullable=False, server_default="PENDING"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_snapshot", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("output_manifest", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("comfy_prompt_id", sa.String(128), nullable=True, unique=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "task IN ('t2v','i2v','fl2v','r2v','v2v','rv2v','mixed')",
            name=op.f("ck_director_runs_task"),
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','DISPATCHING','QUEUED','RUNNING','COLLECTING',"
            "'COMPLETED','FAILED','CANCEL_REQUESTED','CANCELLED')",
            name=op.f("ck_director_runs_status"),
        ),
        sa.CheckConstraint(
            "revision >= 1 AND attempt_count >= 0", name=op.f("ck_director_runs_counters")
        ),
    )
    op.create_index("ix_director_runs_dispatch", "director_runs", ["status", "lease_expires_at", "created_at"])
    op.create_index("ix_director_runs_video_created", "director_runs", ["video_id", "created_at", "id"])
    op.create_index("ix_director_runs_workflow_id", "director_runs", ["workflow_id"])
    op.create_index("ix_director_runs_created_by", "director_runs", ["created_by"])
    op.create_index("ix_director_runs_request_id", "director_runs", ["request_id"])

    op.create_table(
        "director_run_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "director_run_id",
            sa.String(36),
            sa.ForeignKey("director_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="CREATED"),
        sa.Column("client_id", sa.String(128), nullable=False, unique=True),
        sa.Column("comfy_prompt_id", sa.String(128), nullable=True, unique=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "director_run_id", "attempt_no", name="uq_director_run_attempts_number"
        ),
        sa.CheckConstraint("attempt_no >= 1", name=op.f("ck_director_run_attempts_attempt_positive")),
        sa.CheckConstraint(
            "status IN ('CREATED','DISPATCHING','QUEUED','RUNNING','COLLECTING',"
            "'COMPLETED','FAILED','CANCELLED','UNKNOWN')",
            name=op.f("ck_director_run_attempts_status"),
        ),
    )
    op.create_index(
        "ix_director_run_attempts_created", "director_run_attempts", ["created_at", "id"]
    )

    op.create_table(
        "director_run_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "director_run_id",
            sa.String(36),
            sa.ForeignKey("director_runs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scene_id", sa.String(36), nullable=False),
        sa.Column("scene_generation_id", sa.String(36), nullable=False),
        sa.Column("member_index", sa.Integer(), nullable=False),
        sa.Column("continuity", sa.String(16), nullable=False, server_default="CUT"),
        sa.Column("status", sa.String(16), nullable=False, server_default="CREATED"),
        sa.Column("output_asset_id", sa.String(36), sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("output_metadata", json_type, nullable=False, server_default=sa.text("'{}'")),
        sa.ForeignKeyConstraint(
            ["scene_generation_id", "scene_id"],
            ["scene_generations.id", "scene_generations.scene_id"],
            name="fk_director_member_generation_same_scene",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("director_run_id", "member_index", name="uq_director_run_members_order"),
        sa.UniqueConstraint("director_run_id", "scene_id", name="uq_director_run_members_scene"),
        sa.UniqueConstraint("scene_generation_id", name="uq_director_run_members_generation"),
        sa.CheckConstraint("member_index >= 0", name=op.f("ck_director_run_members_order_nonnegative")),
        sa.CheckConstraint(
            "continuity IN ('CUT','CONTINUOUS')", name=op.f("ck_director_run_members_continuity")
        ),
        sa.CheckConstraint(
            "status IN ('CREATED','RUNNING','COMPLETED','FAILED','CANCELLED')",
            name=op.f("ck_director_run_members_status"),
        ),
    )
    op.create_index("ix_director_run_members_scene_id", "director_run_members", ["scene_id"])
    op.create_index(
        "ix_director_run_members_output_asset_id", "director_run_members", ["output_asset_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM workflow_registry WHERE quality_profile IN ('BASE','HD','FULL_HD_REFINED','CUSTOM')")):
        raise ValueError("Cannot downgrade while Director quality profile records exist")
    if bind.scalar(sa.text("SELECT count(*) FROM director_runs")):
        raise ValueError("Cannot downgrade while durable Director aggregate runs exist")
    if bind.scalar(
        sa.text(
            "SELECT count(*) FROM workflow_registry WHERE mode IN ('fl2v','v2v','rv2v')"
        )
    ) or bind.scalar(
        sa.text("SELECT count(*) FROM scene_generations WHERE mode IN ('fl2v','v2v','rv2v')")
    ):
        raise ValueError("Cannot downgrade while new Director mode records exist")
    op.drop_table("director_run_members")
    op.drop_table("director_run_attempts")
    op.drop_table("director_runs")
    change_mode("scene_generations", OLD_MODES)
    change_mode("workflow_registry", OLD_MODES)
    with op.batch_alter_table("workflow_registry") as batch:
        batch.drop_constraint(op.f("ck_workflow_registry_quality_profile"), type_="check")
        batch.create_check_constraint(op.f("ck_workflow_registry_quality_profile"),
            "quality_profile IN ('DRAFT','STANDARD','HIGH')")
