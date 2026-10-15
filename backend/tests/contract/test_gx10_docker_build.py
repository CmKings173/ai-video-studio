"""Static source-build contracts; no Docker daemon, network or database required."""

import re
import shlex
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


def _included(context: str, path: str) -> bool:
    """Evaluate the literal, *, and ** ignore rules used by these contexts.

    Match ancestors too: ignoring a directory excludes all its descendants.
    This deliberately is not a general Dockerignore parser.
    """
    included = True
    parts = path.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for line in (ROOT / context / ".dockerignore").read_text().splitlines():
        rule = line.strip()
        if not rule or rule.startswith("#"):
            continue
        negate = rule.startswith("!")
        rule = rule.removeprefix("!").strip("/")
        regex = re.escape(rule).replace(r"\*\*/", "(?:.*/)?")
        regex = regex.replace(r"\*\*", ".*").replace(r"\*", "[^/]*")
        if any(re.fullmatch(regex, candidate) for candidate in ancestors):
            included = negate
    return included


def test_backend_installs_locked_runtime_without_build_backend_resolution():
    dockerfile = (BACKEND / "Dockerfile").read_text()
    commands = dockerfile.replace("\\\n", " ").splitlines()
    syncs = [shlex.split(line[4:]) for line in commands if line.startswith("RUN uv sync ")]
    assert syncs, "uv pip install . ignores uv.lock; use locked project synchronization"
    for command in syncs:
        assert {"--locked", "--no-dev", "--no-install-project"} <= set(command)
    assert "uv pip install" not in dockerfile
    assert "UV_PROJECT_ENVIRONMENT=/opt/venv" in dockerfile
    assert 'PATH="/opt/venv/bin:$PATH"' in dockerfile
    # Source is already on PYTHONPATH; installing it would resolve unlocked hatchling>=1.27.
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert project["build-system"]["build-backend"] == "hatchling.build"
    assert "PYTHONPATH=/app" in dockerfile
    assert dockerfile.index("COPY pyproject.toml uv.lock") < dockerfile.index("RUN uv sync")
    assert dockerfile.index("RUN uv sync") < dockerfile.index("COPY apps")


def test_uv_pin_preserves_target_architecture():
    dockerfile = (BACKEND / "Dockerfile").read_text()
    assert re.search(r"FROM ghcr\.io/astral-sh/uv:0\.11\.32@sha256:[0-9a-f]{64} AS uv", dockerfile)
    assert not re.search(r"--platform=|x86_64|amd64|aarch64", dockerfile)
    assert "COPY --from=uv /uv /uvx /bin/" in dockerfile
    lock = tomllib.loads((BACKEND / "uv.lock").read_text())
    for name in ("asyncpg", "pillow", "pydantic-core", "psycopg-binary", "uvloop"):
        package = next(p for p in lock["package"] if p["name"] == name)
        assert any("aarch64" in w["url"] for w in package["wheels"]), name


@pytest.mark.parametrize("context", ["backend", "frontend"])
@pytest.mark.parametrize("path", [
    ".env", ".env.production", "nested/.env.local", ".venv/bin/python",
    "node_modules/native.node", ".git/config", "nested/.git/config",
    ".pytest-e-approved-regression-114e8b/db", "pytest-of-Admin/pytest-1/data",
    ".ruff_cache/data", ".tmp/output", "tests/fixture.db", "coverage/report",
    "nested-repo/pyproject.toml", "nested-repo/src/main.py", "private.pem",
])
def test_context_excludes_local_inputs(context, path):
    assert not _included(context, path), f"{context} leaks {path}"


@pytest.mark.parametrize("context,directory", [("backend", "apps"), ("frontend", "public")])
@pytest.mark.parametrize("path", [
    ".env", "nested/.env.production", "secret.pem", "nested/secret.key",
    ".git/config", "node_modules/native.node", ".venv/bin/python",
    "__pycache__/module.pyc", ".pytest-e-proof/data", "pytest-of-Admin/data",
])
def test_exclusions_apply_inside_allowed_directories(context, directory, path):
    assert not _included(context, f"{directory}/{path}")


def test_existing_h3_runtime_workflows_remain_available():
    workflows = list((BACKEND / "workflows" / "h3").glob("*.json"))
    assert workflows, "H3 workflow inputs must remain present"
    for workflow in workflows:
        assert _included("backend", workflow.relative_to(BACKEND).as_posix())


@pytest.mark.parametrize("path", [
    "apps/api/app/main.py", "workers/generation.py", "migrations/env.py",
    "alembic.ini", "pyproject.toml", "uv.lock", "workflows/runtime.json",
    "apps/api/app/providers/minimax_h3_director/workflow_builder.py",
])
def test_backend_runtime_inputs_remain_available(path):
    assert _included("backend", path)


@pytest.mark.parametrize("path", [
    "package.json", "yarn.lock", "next.config.ts", "tsconfig.json", "next-env.d.ts",
    "postcss.config.mjs", "tailwind.config.ts", "app/page.tsx", "components/video.tsx",
    "lib/api.ts", "public/logo.svg",
])
def test_frontend_build_inputs_remain_available(path):
    assert _included("frontend", path)
