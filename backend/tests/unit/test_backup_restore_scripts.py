"""Execute copied production scripts against a native Docker shim, never services."""
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell is unavailable")

STUB = r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ["DOCKER_CALLS"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[0] != "compose" or "-f" not in args or "--env-file" not in args:
    sys.exit(90)
for option in ["-f", "--env-file"]:
    path = Path(args[args.index(option) + 1])
    if not path.is_absolute() or not path.is_file():
        sys.exit(90)
step = next((label for command, label in [
    ("run", "minio"), ("cp", "copy"), ("createdb", "create"), ("pg_restore", "restore")
] if command in args), "dump")
if os.environ.get("DOCKER_FAIL") == step:
    sys.exit(23)
if "run" in args:
    index = args.index("api")
    if "--user" not in args[:index]:
        sys.exit(95)
    if os.name != "nt" and args[args.index("--user") + 1] != f"{os.geteuid()}:{os.getegid()}":
        sys.exit(96)
    if "-v" in args[index:] or "/infra-scripts/minio_snapshot.py" not in args:
        sys.exit(91)
    mounts = [args[i+1] for i, a in enumerate(args[:index]) if a == "-v"]
    if not any(":/infra-scripts:ro" in v for v in mounts):
        sys.exit(92)
    if "backup" in args:
        target = Path(next(v.split(":/backup")[0] for v in mounts if ":/backup" in v))
        manifest = {"format_version": 1, "source_bucket": "ai-video",
                    "object_count": 0, "total_bytes": 0, "objects": []}
        if os.environ.get("DOCKER_OMIT") != "manifest":
            (target / "minio" / "manifest.json").write_text(json.dumps(manifest))
        if os.environ.get("DOCKER_OMIT") == "invalid-report":
            manifest["object_count"] = 1
        if os.environ.get("DOCKER_OMIT") != "report":
            (target / "minio-report.json").write_text(json.dumps(manifest))
    else:
        if not any(":/snapshot:ro" in v for v in mounts):
            sys.exit(93)
        target = Path(next(v.split(":/reports")[0] for v in mounts if ":/reports" in v))
        report = {"status": "RESTORED", "target_bucket": args[args.index("--bucket") + 1],
                  "object_count": 0, "total_bytes": 0}
        if os.environ.get("DOCKER_OMIT") == "invalid-report":
            report["status"] = "PARTIAL"
        if os.environ.get("DOCKER_OMIT") != "report":
            (target / "restore-report.json").write_text(json.dumps(report))
elif "cp" in args and args[-2].startswith("postgres:"):
    Path(args[-1]).write_bytes(b"" if os.environ.get("DOCKER_OMIT") == "dump" else b"PGDMPstub")
'''


@pytest.fixture
def scripts(tmp_path):
    scripts_dir = tmp_path / "infra" / "scripts"
    scripts_dir.mkdir(parents=True)
    for source in (REPO / "infra" / "scripts").glob("*.ps1"):
        shutil.copy(source, scripts_dir)
    (scripts_dir.parent / "compose.yaml").write_text("services: {}")
    (scripts_dir.parent.parent / ".env").write_text("APP_ENV=development\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "docker_stub.py"
    stub.write_text(STUB)
    if os.name == "nt":
        (binary / "docker.cmd").write_text(
            f'@echo off\n"{sys.executable}" "{stub}" %*\n'
        )
    else:
        launcher = binary / "docker"
        launcher.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(stub))} "$@"\n'
        )
        launcher.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "DOCKER_CALLS": str(calls), "TEMP": str(tmp_path), "TMP": str(tmp_path),
           "TMPDIR": str(tmp_path)}

    def run(name, *args, fail="", omit=""):
        return subprocess.run(
            [SHELL, "-NoProfile", "-NonInteractive", "-File", str(scripts_dir / name), *args],
            cwd=tmp_path, env={**env, "DOCKER_FAIL": fail, "DOCKER_OMIT": omit},
            capture_output=True, text=True, timeout=30,
        )

    return run, calls


def snapshot(tmp_path):
    backup = tmp_path / "snapshot"
    (backup / "minio").mkdir(parents=True)
    (backup / "studio.dump").write_bytes(b"PGDMPstub")
    manifest = {"format_version": 1, "source_bucket": "ai-video", "object_count": 0,
                "total_bytes": 0, "objects": []}
    (backup / "minio" / "manifest.json").write_text(json.dumps(manifest))
    (backup / "minio-report.json").write_text(json.dumps(manifest))
    entries = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(backup)}"
               for p in backup.rglob("*") if p.is_file()]
    (backup / "manifest.sha256").write_text("\n".join(entries))
    return backup


def test_backup_mounts_helpers_before_service_and_verifies_artifacts(scripts, tmp_path):
    run, calls = scripts
    result = run("backup.ps1", "-Destination", str(tmp_path / "backups"))
    assert result.returncode == 0, result.stderr
    status_path = tmp_path / "backups" / "backup-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    assert (Path(status["backup_path"]) / "manifest.sha256").stat().st_size > 0
    assert any("run" in json.loads(line) for line in calls.read_text().splitlines())


@pytest.mark.parametrize("fail", ["dump", "copy", "minio"])
def test_backup_native_failure_never_writes_success(scripts, tmp_path, fail):
    run, calls = scripts
    result = run("backup.ps1", "-Destination", str(tmp_path / "backups"), fail=fail)
    assert result.returncode != 0
    assert not (tmp_path / "backups" / "backup-status.json").exists()
    assert len(calls.read_text().splitlines()) == {"dump": 1, "copy": 2, "minio": 3}[fail]


@pytest.mark.parametrize("omit", ["dump", "report", "manifest", "invalid-report"])
def test_backup_missing_artifact_never_writes_success(scripts, tmp_path, omit):
    run, _ = scripts
    result = run("backup.ps1", "-Destination", str(tmp_path / "backups"), omit=omit)
    assert result.returncode != 0
    assert not (tmp_path / "backups" / "backup-status.json").exists()


def test_restore_is_disposable_and_mounts_helper_and_readonly_snapshot(scripts, tmp_path):
    run, calls = scripts
    backup = snapshot(tmp_path)
    before = {str(p): p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    result = run("restore.ps1", "-Backup", str(backup), "-Force")
    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    create = next(args for args in recorded if "createdb" in args)
    assert create[-1].startswith("studio_restore_")
    restore = next(args for args in recorded if "pg_restore" in args)
    assert "--exit-on-error" in restore
    minio = next(args for args in recorded if "run" in args)
    assert minio[minio.index("--bucket") + 1].startswith("ai-video-restore-")
    assert "--replace" not in minio
    assert before == {str(p): p.read_bytes() for p in backup.rglob("*") if p.is_file()}


@pytest.mark.parametrize("fail", ["copy", "create", "restore", "minio"])
def test_restore_native_failure_does_not_announce_success(scripts, tmp_path, fail):
    run, _ = scripts
    result = run("restore.ps1", "-Backup", str(snapshot(tmp_path)), "-Force", fail=fail)
    assert result.returncode != 0
    assert "Restored PostgreSQL" not in result.stdout


@pytest.mark.parametrize("omit", ["report", "invalid-report"])
def test_restore_missing_report_does_not_announce_success(scripts, tmp_path, omit):
    run, _ = scripts
    result = run("restore.ps1", "-Backup", str(snapshot(tmp_path)), "-Force", omit=omit)
    assert result.returncode != 0
    assert "Restored PostgreSQL" not in result.stdout


def test_restore_rejects_corrupt_object_manifest_before_docker(scripts, tmp_path):
    run, calls = scripts
    backup = snapshot(tmp_path)
    (backup / "minio" / "manifest.json").write_text("corrupt")
    result = run("restore.ps1", "-Backup", str(backup), "-Force")
    assert result.returncode != 0
    assert not calls.exists()


@pytest.mark.parametrize("target", [("-Database", "studio"), ("-Bucket", "ai-video")])
def test_restore_rejects_live_targets_before_docker(scripts, tmp_path, target):
    run, calls = scripts
    result = run("restore.ps1", "-Backup", str(snapshot(tmp_path)), "-Force", *target)
    assert result.returncode != 0
    assert not calls.exists()


def test_restore_requires_explicit_force_before_docker(scripts, tmp_path):
    run, calls = scripts
    result = run("restore.ps1", "-Backup", str(snapshot(tmp_path)))
    assert result.returncode != 0
    assert not calls.exists()


@pytest.mark.parametrize("script", ["backup.ps1", "restore.ps1"])
def test_every_compose_call_pins_environment_and_helper_user(scripts, tmp_path, script):
    run, calls = scripts
    args = ("-Destination", str(tmp_path / "backups")) if script == "backup.ps1" else (
        "-Backup", str(snapshot(tmp_path)), "-Force"
    )
    result = run(script, *args)
    assert result.returncode == 0, result.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    for call in recorded:
        assert "--env-file" in call
        assert Path(call[call.index("--env-file") + 1]) == tmp_path / ".env"
    helper = next(call for call in recorded if "run" in call)
    assert helper.index("--user") < helper.index("api")
    user = helper[helper.index("--user") + 1]
    expected = "0:0" if os.name == "nt" else f"{os.geteuid()}:{os.getegid()}"
    assert user == expected
    assert all("--user" not in call for call in recorded if "run" not in call)


@pytest.mark.skipif(os.name == "nt", reason="Linux UID/mode boundary requires a POSIX host")
def test_linux_helper_writes_as_output_owner_without_world_write(scripts, tmp_path):
    run, calls = scripts
    result = run("backup.ps1", "-Destination", str(tmp_path / "backups"))
    assert result.returncode == 0, result.stderr
    helper = next(json.loads(line) for line in calls.read_text().splitlines()
                  if "run" in json.loads(line))
    selected_uid = int(helper[helper.index("--user") + 1].split(":")[0])
    report = next((tmp_path / "backups").glob("*/minio-report.json"))
    assert selected_uid == report.stat().st_uid == os.geteuid()
    assert not report.parent.stat().st_mode & 0o002
    image_uid = image_gid = 10001
    directory = report.parent.stat()
    if selected_uid != image_uid:
        image_write_bit = 0o020 if directory.st_gid == image_gid else 0o002
        assert not directory.st_mode & image_write_bit, "image UID lacks output write access"
    # The production image's studio UID need not own this directory. The scoped
    # --user option is what makes the actual helper write as its output owner.
    assert os.access(report.parent, os.W_OK)


def test_real_compose_interpolation_uses_synthetic_env_outside_cwd(tmp_path):
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI unavailable; probe never uses the daemon")
    compose = tmp_path / "infra" / "compose.yaml"
    compose.parent.mkdir()
    shutil.copy(REPO / "infra" / "compose.yaml", compose)
    values = {
        "POSTGRES_PASSWORD": "synthetic-db-password",
        "MINIO_PUBLIC_ENDPOINT": "http://synthetic.invalid:9000",
        "MINIO_ACCESS_KEY": "synthetic-access", "MINIO_SECRET_KEY": "synthetic-secret",
        "COOKIE_SECURE": "false", "BOOTSTRAP_ADMIN_EMAIL": "synthetic@example.test",
        "BOOTSTRAP_ADMIN_PASSWORD": "synthetic-admin-password",
        "ALLOWED_ORIGINS": '["http://synthetic.invalid"]', "METRICS_TOKEN": "x" * 32,
    }
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    caller = tmp_path / "caller"
    caller.mkdir()
    config = tmp_path / "docker-config"
    config.mkdir()
    env = {key: value for key, value in os.environ.items() if key.upper() not in values}
    env.update(DOCKER_CONFIG=str(config))
    negative = subprocess.run(
        [docker, "compose", "-f", str(compose), "config", "--quiet"], cwd=caller,
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert negative.returncode != 0, "the fixture must expose CWD-dependent interpolation"
    helper = str(REPO / "infra" / "scripts" / "backup_common.ps1").replace("'", "''")
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        "param($Docker, $ComposeFile, $EnvironmentFile)\n$ErrorActionPreference = 'Stop'\n"
        f". '{helper}'\n"
        "$options = Get-ComposeArguments $ComposeFile $EnvironmentFile\n"
        "& $Docker @options config --quiet\nexit $LASTEXITCODE\n"
    )
    positive = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-File", str(probe), docker,
         str(compose), str(env_file)], cwd=caller, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert positive.returncode == 0, positive.stderr
