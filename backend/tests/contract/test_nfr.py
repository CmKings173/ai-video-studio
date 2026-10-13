import re
from pathlib import Path

import pytest

from apps.api.app.core.config import Settings
from apps.api.app.core.metrics import MetricsRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """These contracts exercise defaults, independently of operator environment files."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)


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
            jwt_secret="nfr-contract-test-signing-secret-at-least-32-bytes",
            internal_proxy_token="i" * 32,
            studio_ingress_token="s" * 32,
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
            jwt_secret="nfr-contract-test-signing-secret-at-least-32-bytes",
            internal_proxy_token="i" * 32,
            studio_ingress_token="s" * 32,
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
            jwt_secret="nfr-contract-test-signing-secret-at-least-32-bytes",
            internal_proxy_token="i" * 32,
            studio_ingress_token="s" * 32,
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
    assert "JWT_SECRET: ${JWT_SECRET:-}" in compose
    assert "JWT_SECRET: ${JWT_SECRET:?" not in compose
    assert "${POSTGRES_PASSWORD:-studio}" not in compose
    assert "${MINIO_SECRET_KEY:-studio-change-me}" not in compose


def test_compose_exposes_jwt_signing_secret_only_to_api():
    compose = (REPO_ROOT / "infra" / "compose.yaml").read_text(encoding="utf-8")
    assert "env_file:" not in compose
    shared_image = compose.split("x-api-image:", 1)[1].split("x-api-signing-env:", 1)[0]
    assert "JWT_SECRET:" not in shared_image

    signing_environment = compose.split("x-api-signing-env:", 1)[1].split("services:", 1)[0]
    assert "JWT_SECRET: ${JWT_SECRET:-}" in signing_environment

    def service_block(name: str) -> str:
        match = re.search(
            rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^volumes:\n|\Z)",
            compose,
        )
        assert match is not None
        return match.group(1)

    assert "environment: *api-signing-env" in service_block("api")
    assert "JWT_SECRET" not in service_block("frontend")
    for name in ("migrate", "dispatcher", "assembler", "asset-validator", "reconciler"):
        assert "<<: *api-image" in service_block(name)
        assert "JWT_SECRET" not in service_block(name)

    # Keep every backend setting explicitly mapped after removing the broad
    # env_file that exposed the signing key to every service.
    for field_name in Settings.model_fields:
        if field_name == "jwt_secret":
            continue
        env_name = field_name.upper()
        assert re.search(rf"(?m)^\s+{env_name}:", shared_image), env_name
    assert "RETENTION_UNSELECTED_GENERATION_DAYS:" in shared_image
    assert "NEXT_PUBLIC_API_BASE_URL:" in service_block("frontend")
