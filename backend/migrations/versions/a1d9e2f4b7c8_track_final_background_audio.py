"""track final-video background audio as a relational immutable reference

Revision ID: a1d9e2f4b7c8
Revises: 7b2c4e8d91f0
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1d9e2f4b7c8"
down_revision: str | None = "7b2c4e8d91f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("final_videos", recreate="always") as batch:
            batch.add_column(sa.Column("background_audio_asset_id", sa.String(length=36)))
            batch.create_foreign_key(
                "fk_final_videos_background_audio_asset_id_assets",
                "assets",
                ["background_audio_asset_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    else:
        op.add_column(
            "final_videos",
            sa.Column("background_audio_asset_id", sa.String(length=36), nullable=True),
        )
        op.create_foreign_key(
            "fk_final_videos_background_audio_asset_id_assets",
            "final_videos",
            "assets",
            ["background_audio_asset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_final_videos_background_audio_asset_id",
        "final_videos",
        ["background_audio_asset_id"],
        unique=False,
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_final_manifest_mutation() RETURNS trigger AS $$
            BEGIN
                IF NEW.video_id IS DISTINCT FROM OLD.video_id
                   OR NEW.version_no IS DISTINCT FROM OLD.version_no
                   OR NEW.manifest IS DISTINCT FROM OLD.manifest
                   OR NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash
                   OR NEW.assembly_config IS DISTINCT FROM OLD.assembly_config
                   OR NEW.background_audio_asset_id IS DISTINCT FROM OLD.background_audio_asset_id THEN
                    RAISE EXCEPTION 'immutable final assembly manifest cannot be changed';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )


def downgrade() -> None:
    op.drop_index("ix_final_videos_background_audio_asset_id", table_name="final_videos")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("final_videos", recreate="always") as batch:
            batch.drop_constraint("fk_final_videos_background_audio_asset_id_assets")
            batch.drop_column("background_audio_asset_id")
    else:
        op.drop_constraint(
            "fk_final_videos_background_audio_asset_id_assets",
            "final_videos",
            type_="foreignkey",
        )
        op.drop_column("final_videos", "background_audio_asset_id")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_final_manifest_mutation() RETURNS trigger AS $$
            BEGIN
                IF NEW.video_id IS DISTINCT FROM OLD.video_id
                   OR NEW.version_no IS DISTINCT FROM OLD.version_no
                   OR NEW.manifest IS DISTINCT FROM OLD.manifest
                   OR NEW.manifest_hash IS DISTINCT FROM OLD.manifest_hash
                   OR NEW.assembly_config IS DISTINCT FROM OLD.assembly_config THEN
                    RAISE EXCEPTION 'immutable final assembly manifest cannot be changed';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
