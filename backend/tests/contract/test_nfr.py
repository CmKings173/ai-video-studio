from pathlib import Path

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.core.metrics import MetricsRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_prometheus_metrics_have_measurable_queue_and_backup_series():
    registry = MetricsRegistry()
    registry.set("studio_generation_queue_oldest_age_seconds", 12)
    registry.set("studio_backup_age_seconds", -1)
    output = registry.render()
    assert "studio_generation_queue_oldest_age_seconds 12" in output
    assert "studio_backup_age_seconds -1" in output


def test_production_rejects_placeholder_metrics_token():
    with pytest.raises(ValueError, match="metrics token"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://studio:strong-db-password@localhost:5432/studio",
            cookie_secure=True,
            minio_access_key="safe-access-key",
            minio_secret_key="safe-secret-key",
            bootstrap_admin_password="a" * 16,
            metrics_token="replace-with-a-random-32-character-token",
        )


def test_production_rejects_default_database_credentials():
    with pytest.raises(ValueError, match="database"):
        Settings(
            app_env="production",
            cookie_secure=True,
            minio_access_key="safe-access-key",
            minio_secret_key="safe-secret-key",
            bootstrap_admin_password="a" * 16,
            metrics_token="m" * 32,
        )


def test_production_requires_bootstrap_password():
    with pytest.raises(ValueError, match="bootstrap"):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://studio:strong-db-password@localhost:5432/studio",
            cookie_secure=True,
            minio_access_key="safe-access-key",
            minio_secret_key="safe-secret-key",
            metrics_token="m" * 32,
        )


def test_compose_requires_runtime_secrets():
    compose = (REPO_ROOT / "infra" / "compose.yaml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD:?" in compose
    assert "MINIO_SECRET_KEY:?" in compose
    assert "BOOTSTRAP_ADMIN_PASSWORD:?" in compose
    assert "METRICS_TOKEN:?" in compose
    assert "${POSTGRES_PASSWORD:-studio}" not in compose
    assert "${MINIO_SECRET_KEY:-studio-change-me}" not in compose
