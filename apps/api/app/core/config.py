"""Environment-only runtime configuration shared by API and workers."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    database_url: str = "postgresql+asyncpg://studio:studio@localhost:5432/studio"
    minio_endpoint: str = "http://localhost:9000"
    minio_public_endpoint: str = "http://localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ai-video"
    comfyui_base_url: str = "http://host.docker.internal:8188"
    workspace_root: Path = Path("/data/studio")
    workflow_dir: Path = Path("workflows/h3")
    cookie_secure: bool = False
    session_hours: int = Field(default=24, ge=1, le=720)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    lease_seconds: int = Field(default=120, ge=10)
    comfy_timeout_seconds: int = Field(default=3600, ge=30)
    comfy_scoped_interrupt: bool = False
    max_generation_attempts: int = Field(default=2, ge=1, le=5)
    max_assembly_attempts: int = Field(default=2, ge=1, le=5)
    max_pending_prompts: int = Field(default=1, ge=1, le=1)
    min_free_disk_bytes: int = Field(default=5 * 1024**3, ge=0)
    max_upload_bytes: int = Field(default=500 * 1024**2, ge=1)
    max_media_seconds: float = Field(default=600, gt=0)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""
    session_cookie_name: str = "studio_session"
    csrf_header_name: str = "X-CSRF-Token"
    idempotency_hours: int = Field(default=24, ge=1, le=168)
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"
    ffmpeg_timeout_seconds: int = Field(default=900, ge=30)
    sse_poll_seconds: float = Field(default=1.0, ge=0.1, le=10)
    sse_heartbeat_seconds: int = Field(default=20, ge=5, le=60)
    pending_asset_retention_hours: int = Field(default=24, ge=1)
    deleted_asset_retention_hours: int = Field(default=168, ge=1)
    orphan_object_retention_hours: int = Field(default=24, ge=1)
    reconciliation_interval_seconds: int = Field(default=300, ge=10, le=86400)
    request_body_max_bytes: int = Field(default=2 * 1024**2, ge=1024)
    metrics_token: str = ""
    backup_rpo_hours: int = Field(default=24, ge=1, le=168)
    backup_rto_hours: int = Field(default=4, ge=1, le=72)
    retention_failed_hours: int = Field(default=24, ge=1)
    retention_deleting_retry_hours: int = Field(default=1, ge=1)
    retention_batch_size: int = Field(default=200, ge=1, le=1000)
    asset_operation_claim_timeout_seconds: int = Field(default=900, ge=60)
    upload_intent_grace_seconds: int = Field(default=900, ge=60)
    backup_status_file: Path | None = None

    @field_validator("database_url")
    @classmethod
    def asynchronous_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if value.startswith("sqlite://"):
            return value.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return value

    @model_validator(mode="after")
    def production_defaults(self) -> "Settings":
        if self.app_env.lower() == "production":
            if not self.cookie_secure:
                raise ValueError("Production requires COOKIE_SECURE=true")
            database = urlsplit(self.database_url)
            if (
                database.scheme != "postgresql+asyncpg"
                or not database.hostname
                or not database.password
            ):
                raise ValueError("Production requires a PostgreSQL database URL with a password")
            if database.password.lower() in {"studio", "password", "change-me", "changeme"}:
                raise ValueError("Production database password is unsafe")
            if not self.minio_access_key or not self.minio_secret_key:
                raise ValueError("Production requires MinIO credentials")
            if self.minio_access_key.lower() in {
                "studio",
                "minio",
                "access",
            } or self.minio_secret_key.lower() in {
                "studio",
                "studio-change-me",
                "change-me",
                "secret",
            }:
                raise ValueError("Production MinIO credentials are unsafe")
            if "*" in self.allowed_origins or not self.allowed_origins:
                raise ValueError("Credentialed CORS requires explicit origins")
            if not self.bootstrap_admin_password or self.bootstrap_admin_password in {
                "change-this-password",
                "password",
                "admin",
            }:
                raise ValueError("Production bootstrap password is unsafe or missing")
            if (
                len(self.metrics_token) < 32
                or self.metrics_token.startswith("replace-with")
                or self.metrics_token.lower() in {"metrics", "change-me"}
            ):
                raise ValueError("Production requires a metrics token of at least 32 characters")
        if self.bootstrap_admin_password and len(self.bootstrap_admin_password) < 12:
            raise ValueError("Bootstrap administrator password must be at least 12 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
