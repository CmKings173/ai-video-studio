"""Offline Compose contracts. No Docker daemon, containers, or live DB access."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "infra/gx10.env.example"
BACKENDS = {"api", "migrate", "dispatcher", "assembler", "asset-validator", "reconciler"}


def compose_config(tmp_path, remove=()):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI unavailable; GX10 config gate was not verified")
    lines = EXAMPLE.read_text(encoding="utf-8").splitlines()
    variables = {
        line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")
    }
    env = {key: value for key, value in os.environ.items() if key not in variables}
    for key in ("COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES", *remove):
        env.pop(key, None)
    env_path = tmp_path / "gx10.env"
    env_path.write_text(
        "\n".join(line for line in lines if line.split("=", 1)[0] not in remove),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            docker, "compose", "--env-file", str(env_path),
            "-f", str(ROOT / "infra/compose.yaml"),
            "-f", str(ROOT / "infra/compose.gx10.yaml"),
            "config", "--format", "json",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )


@pytest.fixture
def model(tmp_path):
    result = compose_config(tmp_path)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_uat_is_arm64_and_uses_project_scoped_storage(model):
    assert model["name"] == "ai-video-studio-gx10-uat"
    assert all(service["platform"] == "linux/arm64" for service in model["services"].values())
    assert set(model["volumes"]) == {"postgres_data", "minio_data", "worker_workspace"}
    for volume in model["volumes"].values():
        assert not volume.get("external")
        assert volume["name"].startswith("ai-video-studio-gx10-uat_")
    for service in model["services"].values():
        assert not service.get("container_name")
        assert service.get("network_mode") != "host"
        assert not service.get("privileged")


def test_only_ingress_and_minio_publish_loopback_ports(model):
    services = model["services"]
    exposed = {name for name, service in services.items() if service.get("ports")}
    assert exposed == {"ingress", "minio"}
    expected = {"ingress": {"18080"}, "minio": {"19000", "19001"}}
    for name in exposed:
        assert {port["published"] for port in services[name]["ports"]} == expected[name]
        assert all(port["host_ip"] == "127.0.0.1" for port in services[name]["ports"])


def test_internal_and_browser_endpoints_are_distinct_and_jwt_is_api_only(model):
    services = model["services"]
    for name in BACKENDS:
        service = services[name]
        env = service["environment"]
        assert env["APP_ENV"] == "production"
        assert env["COOKIE_SECURE"] == "true"
        assert env["MINIO_ENDPOINT"] == "http://minio:9000"
        assert env["MINIO_PUBLIC_ENDPOINT"] == "https://gx10-uat-storage.example.test"
        assert env["MINIO_BUCKET"] == "ai-video-gx10-uat"
        assert env["COMFYUI_BASE_URL"] == "http://host.docker.internal:8188"
        assert "host.docker.internal=host-gateway" in service["extra_hosts"]
    for name, service in services.items():
        assert ("JWT_SECRET" in service.get("environment", {})) == (name == "api")
    assert services["frontend"]["environment"]["API_BACKEND_URL"] == "http://api:8000"


def test_source_builds_and_minio_probe_have_matching_tools(model):
    minio = model["services"]["minio"]
    dockerfile = (Path(minio["build"]["context"]) / "Dockerfile").read_text(encoding="utf-8")
    assert ":latest" not in minio["image"]
    assert "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" in dockerfile
    assert "45521908307306e925c98d629e1c17d78c8b72b6ee242b1bfb1409f7d8ee5841" in dockerfile
    assert "sha256sum -c" in dockerfile
    assert dockerfile.index("mkdir -p /out") < dockerfile.index("go build")
    runtime = dockerfile.split("FROM debian:", 1)[1]
    assert "ca-certificates curl" in runtime
    assert minio["healthcheck"]["test"] == [
        "CMD", "curl", "-f", "http://localhost:9000/minio/health/live",
    ]
    assert "USER minio" in runtime
    for name in BACKENDS:
        assert model["services"][name]["build"]["context"] == str(ROOT / "backend")


@pytest.mark.parametrize("missing", ["GX10_SOURCE_TAG", "POSTGRES_PASSWORD", "MINIO_SECRET_KEY"])
def test_missing_required_configuration_is_rejected(tmp_path, missing):
    result = compose_config(tmp_path, remove=(missing,))
    assert result.returncode != 0
    assert missing in result.stderr
