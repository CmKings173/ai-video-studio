"""add concurrency guards and foreign-key indexes

Revision ID: 7b2c4e8d91f0
Revises: 0d768b73cbb0
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7b2c4e8d91f0"
down_revision: str | None = "0d768b73cbb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_projects_created_by", "projects", ["created_by"]),
    ("ix_brands_created_by", "brands", ["created_by"]),
    ("ix_products_created_by", "products", ["created_by"]),
    ("ix_assets_created_by", "assets", ["created_by"]),
    ("ix_videos_brand_id", "videos", ["brand_id"]),
    ("ix_videos_created_by", "videos", ["created_by"]),
    ("ix_videos_current_final_video_id", "videos", ["current_final_video_id"]),
    ("ix_scenes_selected_generation_id", "scenes", ["selected_generation_id"]),
    ("ix_scene_generations_workflow_id", "scene_generations", ["workflow_id"]),
    (
        "ix_scene_generations_parent_generation_id",
        "scene_generations",
        ["parent_generation_id"],
    ),
    ("ix_scene_generations_output_asset_id", "scene_generations", ["output_asset_id"]),
    ("ix_scene_generations_created_by", "scene_generations", ["created_by"]),
    ("ix_generation_attempts_created", "generation_attempts", ["created_at", "id"]),
    ("ix_workflow_registry_created_by", "workflow_registry", ["created_by"]),
    ("ix_final_videos_output_asset_id", "final_videos", ["output_asset_id"]),
    ("ix_final_videos_created_by", "final_videos", ["created_by"]),
    ("ix_final_video_scenes_scene_id", "final_video_scenes", ["scene_id"]),
    ("ix_final_video_scenes_generation_id", "final_video_scenes", ["generation_id"]),
    ("ix_final_video_scenes_asset_id", "final_video_scenes", ["asset_id"]),
)


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)
    op.create_index(
        "uq_workflow_registry_enabled_mode",
        "workflow_registry",
        ["mode"],
        unique=True,
        postgresql_where=sa.text("enabled"),
        sqlite_where=sa.text("enabled = 1"),
    )
    op.create_index(
        "uq_final_videos_active_video",
        "final_videos",
        ["video_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('QUEUED','ASSEMBLING','CANCEL_REQUESTED')"),
        sqlite_where=sa.text("status IN ('QUEUED','ASSEMBLING','CANCEL_REQUESTED')"),
    )


def downgrade() -> None:
    op.drop_index("uq_final_videos_active_video", table_name="final_videos")
    op.drop_index("uq_workflow_registry_enabled_mode", table_name="workflow_registry")
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
