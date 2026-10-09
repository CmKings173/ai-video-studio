"""Add indexes for bounded live SSE snapshots."""

import sqlalchemy as sa
from alembic import op


revision = "a6d2e8f4b1c7"
down_revision = "b5d9f1a3c7e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scene_generations_video_active",
        "scene_generations",
        ["video_id", "status", "created_at", "id"],
    )
    op.create_index(
        "ix_final_videos_video_created",
        "final_videos",
        ["video_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_final_videos_video_created", table_name="final_videos")
    op.drop_index("ix_scene_generations_video_active", table_name="scene_generations")
