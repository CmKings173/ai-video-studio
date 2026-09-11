"""Canonical V1 domain schema. Historical generations/assets are never cascade-deleted."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONType, UTCDateTime, new_id, utcnow


class Identity:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class User(Identity, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('ADMIN','EDITOR')", name="role"),)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="EDITOR")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class Project(Identity, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_projects_created_by", "created_by"),
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class Brand(Identity, Base):
    __tablename__ = "brands"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_brands_created_by", "created_by"),
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class Product(Identity, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        Index("ix_products_created_by", "created_by"),
    )
    brand_id: Mapped[str | None] = mapped_column(
        ForeignKey("brands.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class Asset(Identity, Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("kind IN ('IMAGE','VIDEO','AUDIO')", name="kind"),
        CheckConstraint(
            "status IN ('PENDING','PENDING_UPLOAD','VALIDATING','READY','FAILED','DELETED')",
            name="status",
        ),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds > 0", name="duration_positive"
        ),
        CheckConstraint("project_id IS NULL OR product_id IS NULL", name="single_business_scope"),
        Index("ix_assets_status_created_at", "status", "created_at"),
        Index("ix_assets_created_by", "created_by"),
    )
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), index=True
    )
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    role: Mapped[str] = mapped_column(String(64), default="REFERENCE")
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    checksum: Mapped[str | None] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Video(Identity, Base):
    __tablename__ = "videos"
    __table_args__ = (
        CheckConstraint("kind IN ('QUICK_CLIP','LONG_VIDEO')", name="kind"),
        CheckConstraint(
            "status IN ('DRAFT','PLANNING','STORYBOARD_READY','GENERATING',"
            "'SCENES_READY','ASSEMBLING','READY','DIRTY','FAILED')",
            name="status",
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("target_duration > 0", name="duration_positive"),
        ForeignKeyConstraint(
            ["current_final_video_id", "id"],
            ["final_videos.id", "final_videos.video_id"],
            name="fk_videos_current_final_same_video",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        Index("ix_videos_project_created", "project_id", "created_at", "id"),
        Index("ix_videos_brand_id", "brand_id"),
        Index("ix_videos_created_by", "created_by"),
        Index("ix_videos_current_final_video_id", "current_final_video_id"),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"))
    product_id: Mapped[str | None] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), index=True
    )
    brand_id: Mapped[str | None] = mapped_column(ForeignKey("brands.id", ondelete="RESTRICT"))
    title: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16), default="QUICK_CLIP")
    target_duration: Mapped[float] = mapped_column(Float, default=5.0)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="16:9")
    brief: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    current_final_video_id: Mapped[str | None] = mapped_column(String(36))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class Scene(Identity, Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("id", "video_id", name="uq_scenes_id_video"),
        UniqueConstraint("video_id", "scene_order", name="uq_scenes_video_order"),
        CheckConstraint("scene_order >= 0", name="order_nonnegative"),
        CheckConstraint("duration_seconds > 0", name="duration_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        ForeignKeyConstraint(
            ["selected_generation_id", "id"],
            ["scene_generations.id", "scene_generations.scene_id"],
            name="fk_scenes_selected_same_scene",
            use_alter=True,
            ondelete="RESTRICT",
        ),
        Index("ix_scenes_video_order", "video_id", "scene_order", "id"),
        Index("ix_scenes_selected_generation_id", "selected_generation_id"),
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="RESTRICT"))
    scene_order: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, default=5)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    selected_generation_id: Mapped[str | None] = mapped_column(String(36))


class SceneGeneration(Identity, Base):
    __tablename__ = "scene_generations"
    __table_args__ = (
        UniqueConstraint("id", "scene_id", name="uq_scene_generations_id_scene"),
        UniqueConstraint("scene_id", "generation_no", name="uq_scene_generations_scene_number"),
        ForeignKeyConstraint(
            ["scene_id", "video_id"], ["scenes.id", "scenes.video_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["parent_generation_id", "scene_id"],
            ["scene_generations.id", "scene_generations.scene_id"],
            name="fk_generations_parent_same_scene",
            ondelete="RESTRICT",
        ),
        CheckConstraint("mode IN ('t2v','i2v','i2v_first_last','r2v')", name="mode"),
        CheckConstraint(
            "status IN ('CREATED','DISPATCHING','QUEUED','RUNNING','COLLECTING',"
            "'COMPLETED','FAILED','CANCEL_REQUESTED','CANCELLED')",
            name="status",
        ),
        CheckConstraint("operation IN ('ORIGINAL','REGENERATE','VARIATION')", name="operation"),
        CheckConstraint(
            "revision >= 1 AND generation_no >= 1 AND attempt_count >= 0", name="counters"
        ),
        CheckConstraint(
            "progress_current >= 0 AND progress_total >= 0 AND progress_current <= progress_total",
            name="progress",
        ),
        CheckConstraint(
            "parent_generation_id IS NULL OR parent_generation_id <> id", name="not_own_parent"
        ),
        Index("ix_scene_generations_dispatch", "status", "lease_expires_at", "created_at"),
        Index("ix_scene_generations_video_created", "video_id", "created_at", "id"),
        Index("ix_scene_generations_workflow_id", "workflow_id"),
        Index("ix_scene_generations_parent_generation_id", "parent_generation_id"),
        Index("ix_scene_generations_output_asset_id", "output_asset_id"),
        Index("ix_scene_generations_created_by", "created_by"),
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="RESTRICT"))
    scene_id: Mapped[str] = mapped_column(String(36))
    mode: Mapped[str] = mapped_column(String(32))
    workflow_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_registry.id", ondelete="RESTRICT")
    )
    generation_no: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(16), default="ORIGINAL")
    parent_generation_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(24), default="CREATED")
    phase: Mapped[str] = mapped_column(String(64), default="pending")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    comfy_prompt_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    output_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    queued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    progress_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GenerationAttempt(Identity, Base):
    __tablename__ = "generation_attempts"
    __table_args__ = (
        UniqueConstraint("generation_id", "attempt_no", name="uq_generation_attempts_number"),
        CheckConstraint("attempt_no >= 1", name="attempt_positive"),
        CheckConstraint(
            "status IN ('CREATED','DISPATCHING','QUEUED','RUNNING','COLLECTING',"
            "'COMPLETED','FAILED','CANCELLED','UNKNOWN')",
            name="status",
        ),
        Index("ix_generation_attempts_created", "created_at", "id"),
    )
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("scene_generations.id", ondelete="RESTRICT")
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="CREATED")
    client_id: Mapped[str] = mapped_column(String(128), unique=True)
    comfy_prompt_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GenerationAsset(Identity, Base):
    __tablename__ = "generation_assets"
    __table_args__ = (
        UniqueConstraint("generation_id", "role", "order_index", name="uq_generation_assets_slot"),
        CheckConstraint("order_index >= 0", name="order_nonnegative"),
    )
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("scene_generations.id", ondelete="RESTRICT")
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"), index=True)
    role: Mapped[str] = mapped_column(String(64))
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class WorkflowRecord(Identity, Base):
    __tablename__ = "workflow_registry"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_workflow_registry_code_version"),
        CheckConstraint("mode IN ('t2v','i2v','i2v_first_last','r2v')", name="mode"),
        Index("ix_workflow_registry_created_by", "created_by"),
        Index(
            "uq_workflow_registry_enabled_mode",
            "mode",
            unique=True,
            postgresql_where=text("enabled"),
            sqlite_where=text("enabled = 1"),
        ),
    )
    code: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(64))
    workflow: Mapped[dict[str, Any]] = mapped_column(JSONType)
    slots: Mapped[dict[str, Any]] = mapped_column(JSONType)
    required_slots: Mapped[list[str]] = mapped_column(JSONType, default=list)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    workflow_hash: Mapped[str] = mapped_column(String(64))
    slot_map_hash: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class FinalVideo(Identity, Base):
    __tablename__ = "final_videos"
    __table_args__ = (
        UniqueConstraint("id", "video_id", name="uq_final_videos_id_video"),
        UniqueConstraint("video_id", "version_no", name="uq_final_videos_video_version"),
        CheckConstraint(
            "status IN ('QUEUED','ASSEMBLING','READY','FAILED','CANCEL_REQUESTED','CANCELLED')",
            name="status",
        ),
        CheckConstraint(
            "revision >= 1 AND version_no >= 1 AND attempt_count >= 0", name="counters"
        ),
        CheckConstraint(
            "progress_current >= 0 AND progress_total >= 0 AND progress_current <= progress_total",
            name="progress",
        ),
        Index("ix_final_videos_dispatch", "status", "lease_expires_at", "created_at"),
        Index("ix_final_videos_output_asset_id", "output_asset_id"),
        Index("ix_final_videos_background_audio_asset_id", "background_audio_asset_id"),
        Index("ix_final_videos_created_by", "created_by"),
        Index(
            "uq_final_videos_active_video",
            "video_id",
            unique=True,
            postgresql_where=text(
                "status IN ('QUEUED','ASSEMBLING','CANCEL_REQUESTED')"
            ),
            sqlite_where=text(
                "status IN ('QUEUED','ASSEMBLING','CANCEL_REQUESTED')"
            ),
        ),
    )
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="RESTRICT"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONType)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    assembly_config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    output_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT")
    )
    background_audio_asset_id: Mapped[str | None] = mapped_column(
        ForeignKey("assets.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    phase: Mapped[str] = mapped_column(String(64), default="QUEUED")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    progress_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FinalVideoScene(Identity, Base):
    __tablename__ = "final_video_scenes"
    __table_args__ = (
        UniqueConstraint("final_video_id", "scene_order", name="uq_final_video_scenes_order"),
        UniqueConstraint("final_video_id", "scene_id", name="uq_final_video_scenes_scene"),
        CheckConstraint("scene_order >= 0", name="order_nonnegative"),
        ForeignKeyConstraint(
            ["generation_id", "scene_id"],
            ["scene_generations.id", "scene_generations.scene_id"],
            name="fk_final_scene_generation_same_scene",
            ondelete="RESTRICT",
        ),
        Index("ix_final_video_scenes_scene_id", "scene_id"),
        Index("ix_final_video_scenes_generation_id", "generation_id"),
        Index("ix_final_video_scenes_asset_id", "asset_id"),
    )
    final_video_id: Mapped[str] = mapped_column(ForeignKey("final_videos.id", ondelete="RESTRICT"))
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="RESTRICT"))
    scene_order: Mapped[int] = mapped_column(Integer)
    generation_id: Mapped[str] = mapped_column(String(36))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id", ondelete="RESTRICT"))
    asset_checksum: Mapped[str] = mapped_column(String(64))
    transition_config: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)


class IdempotencyKey(Identity, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "operation", "key", name="uq_idempotency_keys_user_operation_key"
        ),
        CheckConstraint("status_code >= 100 AND status_code <= 599", name="status_code"),
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    operation: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    status_code: Mapped[int] = mapped_column(Integer, default=202)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
