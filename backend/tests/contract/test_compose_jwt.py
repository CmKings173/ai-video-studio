import importlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.app.core.config import Settings
from apps.api.app.core.security import AccessTokenConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infra" / "compose.yaml"
NON_API_RUNTIME_SERVICES = {
    "frontend",
    "migrate",
    "dispatcher",
    "assembler",
    "asset-validator",
    "reconciler",
}


def _service_block(compose: str, name: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [\w-]+:\n|^volumes:\n|\Z)",
        compose,
    )
    assert match is not None, f"Compose service {name!r} is missing"
    return match.group(1)


def test_compose_jwt_secret_is_optional_for_config_interpolation():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    signing_env = compose.split("x-api-signing-env:", 1)[1].split("services:", 1)[0]

    assert "JWT_SECRET: ${JWT_SECRET:-}" in signing_env
    assert "JWT_SECRET: ${JWT_SECRET:?" not in compose


def test_only_api_receives_the_jwt_signing_secret():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    shared_image = compose.split("x-api-image:", 1)[1].split("x-api-signing-env:", 1)[0]
    signing_env = compose.split("x-api-signing-env:", 1)[1].split("services:", 1)[0]

    assert "JWT_SECRET:" not in shared_image
    assert "JWT_SECRET: ${JWT_SECRET:-}" in signing_env
    assert "environment: *api-signing-env" in _service_block(compose, "api")

    for service in NON_API_RUNTIME_SERVICES:
        block = _service_block(compose, service)
        assert "api-signing-env" not in block
        assert "JWT_SECRET" not in block


def test_compose_resolves_without_jwt_secret_when_docker_compose_is_available(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is not installed")

    version = subprocess.run(
        [docker, "compose", "version"], capture_output=True, text=True, check=False
    )
    if version.returncode != 0:
        pytest.skip("Docker Compose plugin is not installed")

    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env.pop("JWT_SECRET", None)
    env.update(
        {
            "POSTGRES_PASSWORD": "compose-contract-postgres-password",
            "MINIO_PUBLIC_ENDPOINT": "http://localhost:9000",
            "MINIO_ACCESS_KEY": "compose-contract-minio-user",
            "MINIO_SECRET_KEY": "compose-contract-minio-password",
            "COOKIE_SECURE": "false",
            "BOOTSTRAP_ADMIN_EMAIL": "admin@example.test",
            "BOOTSTRAP_ADMIN_PASSWORD": "compose-contract-admin-password",
            "ALLOWED_ORIGINS": "http://localhost:3000",
            "METRICS_TOKEN": "m" * 40,
        }
    )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(COMPOSE_FILE),
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["api"]["environment"]["JWT_SECRET"] == ""
    for service in NON_API_RUNTIME_SERVICES:
        assert "JWT_SECRET" not in services[service].get("environment", {})


def _production_settings(jwt_secret: str = "") -> Settings:
    return Settings(
        app_env="production",
        jwt_secret=jwt_secret,
        internal_proxy_token="i" * 32,
        studio_ingress_token="s" * 32,
        cookie_secure=True,
        database_url="postgresql+asyncpg://studio:strong-db-password@localhost:5432/studio",
        minio_access_key="safe-access-key",
        minio_secret_key="safe-minio-secret",
        allowed_origins=["https://studio.example.test"],
        bootstrap_admin_password="a" * 16,
        metrics_token="m" * 32,
    )


async def test_production_api_startup_fails_closed_without_jwt_secret(monkeypatch):
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for field_name in Settings.model_fields:
        monkeypatch.delenv(field_name.upper(), raising=False)
        monkeypatch.delenv(field_name, raising=False)

    main = importlib.import_module("apps.api.app.main")
    monkeypatch.setattr(main, "settings", _production_settings())

    with pytest.raises(AccessTokenConfigurationError, match="valid JWT signing secret"):
        async with main.lifespan(main.app):
            pytest.fail("Production API startup continued without a JWT signing secret")


def test_production_settings_reject_a_weak_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _production_settings("weak")
