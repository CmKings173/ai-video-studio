"""Offline contracts for the pinned GX10 Director dependency image."""

import importlib.util
import os
import re
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "infra" / "gx10-director"
_VERIFY_SPEC = importlib.util.spec_from_file_location(
    "gx10_director_verify_runtime", PACKAGE / "verify_runtime.py"
)
_VERIFY_MODULE = importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFY_MODULE)


def _lock_entries():
    lock = (PACKAGE / "requirements.lock").read_text(encoding="utf-8")
    entries = {}
    current = None
    for line in lock.splitlines():
        if not line or line.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\\ ]+) \\", line)
            assert match, f"expected an exact hash-locked requirement: {line}"
            current = match.group(1).lower().replace("_", "-")
            entries[current] = {"version": match.group(2), "hashes": []}
        else:
            match = re.fullmatch(r"\s+--hash=sha256:([0-9a-f]{64})", line)
            assert match, f"expected one SHA-256 wheel hash: {line}"
            entries[current]["hashes"].append(match.group(1))
    return entries


def test_arm64_python310_lock_is_hash_pinned_and_preserves_gpu_packages():
    entries = _lock_entries()

    assert len(entries) >= 25, "lock must enumerate direct and transitive dependencies"
    assert all(len(item["hashes"]) == 1 for item in entries.values())
    assert entries["torchaudio"]["version"] == "2.11.0"
    assert not {"torch", "torchvision", "opencv-python"} & entries.keys()
    for required in (
        "opencv-python-headless", "imageio-ffmpeg", "scenedetect", "ultralytics",
        "numpy", "pillow", "requests", "torchaudio",
    ):
        assert required in entries

    assert _VERIFY_MODULE.locked_packages() == {
        name: item["version"] for name, item in entries.items()
    }


def test_derived_image_uses_offline_locked_wheels_and_keeps_torch_stack():
    dockerfile = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG BASE_IMAGE=comfyui:local" in dockerfile
    assert "ARG EXPECTED_TORCH=2.14.1+cu130" in dockerfile
    assert "ARG EXPECTED_TORCHVISION=0.29.1+cu130" in dockerfile
    assert "--no-index" in dockerfile
    assert "--no-deps" in dockerfile
    assert "--require-hashes" in dockerfile
    smoke = (PACKAGE / "verify_runtime.py").read_text(encoding="utf-8")
    assert "torchaudio.functional.resample" in smoke
    assert "torch.tensor" in smoke and "resampled.detach().cpu().reshape(-1).tolist()" in smoke
    assert "signal_similarity" in smoke
    assert "verify_opencv_provider" in smoke
    assert "verify_pip_check" in smoke
    assert "verify_director_imports" in smoke
    for module_name in ("ultralytics", "scenedetect", "imageio_ffmpeg"):
        assert module_name in smoke
    assert "torch.cuda" not in smoke
    assert "ffmpeg" in dockerfile and "ffprobe" in dockerfile
    assert "torch==" not in dockerfile and "torchvision==" not in dockerfile
    assert "APT_SNAPSHOT=20261014T000000Z" in dockerfile
    assert "FFMPEG_VERSION=7:4.4.2-0ubuntu0.22.04.1" in dockerfile
    assert set(_VERIFY_MODULE.BASE_RUNTIME_PACKAGES) == {"scipy", "safetensors", "aiohttp"}


def test_apt_one_line_entries_use_list_format_consistently():
    dockerfile = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
    source_paths = re.findall(r"/tmp/gx10-director\.(?:sources|list)", dockerfile)

    # One-line deb entries require .list; .sources is reserved for Deb822.
    assert source_paths == ["/tmp/gx10-director.list"] * 4
    assert dockerfile.count('"deb [check-valid-until=no]') == 3
    assert "-o Dir::Etc::sourceparts=-" in dockerfile


@pytest.mark.parametrize("override, error", [
    ({}, None),
    ({"torch_metadata": "2.14.0"}, "runtime package version mismatch: torch"),
    ({"torchvision_metadata": "0.29.0"}, "runtime package version mismatch: torchvision"),
    ({"torch_runtime": "2.14.1+cpu"}, "torch/torchvision stack changed"),
    ({"torchvision_runtime": "0.29.1+cpu"}, "torch/torchvision stack changed"),
    ({"cuda": "12.9"}, "CUDA build changed"),
    ({"numpy_metadata": "2.2.6+vendor"}, "locked package version mismatch: numpy"),
])
def test_nvidia_metadata_and_exact_runtime_integrity(monkeypatch, override, error):
    verifier = _VERIFY_MODULE
    monkeypatch.setenv("EXPECTED_TORCH", "2.14.1+cu130")
    monkeypatch.setenv("EXPECTED_TORCHVISION", "0.29.1+cu130")
    monkeypatch.setenv("EXPECTED_CUDA", "13.0")
    monkeypatch.setattr(verifier.platform, "python_implementation", lambda: "CPython")
    monkeypatch.setattr(verifier.platform, "system", lambda: "Linux")
    monkeypatch.setattr(verifier.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(sys, "version_info", (3, 10, 0))
    versions = verifier.locked_packages() | {
        "torch": override.get("torch_metadata", "2.14.1"),
        "torchvision": override.get("torchvision_metadata", "0.29.1"),
    }
    if "numpy_metadata" in override:
        versions["numpy"] = override["numpy_metadata"]
    monkeypatch.setattr(verifier.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(verifier, "verify_base_runtime_packages", lambda: {})
    monkeypatch.setattr(verifier, "verify_opencv_provider", lambda: {})
    monkeypatch.setattr(verifier, "verify_base_runtime_imports", lambda: None)
    monkeypatch.setattr(verifier, "verify_director_imports", lambda: ())

    def reached_resampler(*args, **kwargs):
        raise RuntimeError("version checks passed; reached resampler")

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        __version__=override.get("torch_runtime", "2.14.1+cu130"),
        version=SimpleNamespace(cuda=override.get("cuda", "13.0")),
        tensor=reached_resampler, float32=object(),
    ))
    monkeypatch.setitem(sys.modules, "torchvision", SimpleNamespace(
        __version__=override.get("torchvision_runtime", "0.29.1+cu130"),
    ))
    monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace())
    if error is None:
        with pytest.raises(RuntimeError, match="version checks passed; reached resampler"):
            verifier.main()
    else:
        with pytest.raises(SystemExit, match=error):
            verifier.main()


def test_runtime_verifier_fails_closed_when_comfyui_base_dependency_is_missing():
    def missing(name):
        if name == "scipy":
            raise ModuleNotFoundError(name)
        return "1.0"

    with pytest.raises(SystemExit, match="missing ComfyUI base dependency: scipy"):
        _VERIFY_MODULE.verify_base_runtime_packages(missing)

    imported = []
    _VERIFY_MODULE.verify_base_runtime_imports(imported.append)
    assert imported == ["scipy", "safetensors", "aiohttp", "PIL"]

    def broken_import(name):
        if name == "safetensors":
            raise ImportError(name)
        return object()

    with pytest.raises(SystemExit, match="cannot be imported: safetensors"):
        _VERIFY_MODULE.verify_base_runtime_imports(broken_import)


def test_director_import_smoke_uses_temporary_ultralytics_config(monkeypatch):
    monkeypatch.setenv("YOLO_CONFIG_DIR", "preserve-this-value")
    seen = []

    def importer(name):
        if name == "ultralytics":
            config_dir = Path(os.environ["YOLO_CONFIG_DIR"])
            seen.append(config_dir.exists())
            assert config_dir.name.startswith("gx10-ultralytics-config-")
        return object()

    imported = _VERIFY_MODULE.verify_director_imports(importer)
    assert imported == (
        "PIL", "cv2", "imageio_ffmpeg", "scenedetect", "numpy", "ultralytics",
    )
    assert seen == [True]
    assert os.environ["YOLO_CONFIG_DIR"] == "preserve-this-value"


@pytest.mark.parametrize("module_name", ["imageio_ffmpeg", "scenedetect", "ultralytics"])
def test_director_import_smoke_fails_closed(module_name):
    def broken_import(name):
        if name == module_name:
            raise ImportError(name)
        return object()

    with pytest.raises(SystemExit, match=f"cannot be imported: {module_name}"):
        _VERIFY_MODULE.verify_director_imports(broken_import)


def test_opencv_headless_is_one_explicit_provider_for_both_consumers():
    def package_version(name):
        if name == "opencv-python-headless":
            return "5.0.0.93"
        raise PackageNotFoundError(name)

    provider = _VERIFY_MODULE.verify_opencv_provider(
        package_version, _alias_requirements
    )
    assert provider == {
        "distribution": "opencv-python-headless", "version": "5.0.0.93",
        "satisfies": {
            "ultralytics": "opencv-python!=4.13.0.90,>=4.7.0",
            "scenedetect": "opencv-python",
        },
    }

    def too_old_headless(name):
        if name == "opencv-python-headless":
            return "4.5.0"
        raise PackageNotFoundError(name)

    with pytest.raises(SystemExit, match="does not satisfy ultralytics"):
        _VERIFY_MODULE.verify_opencv_provider(
            too_old_headless, lambda _: ["opencv-python>=4.7.0"]
        )


@pytest.mark.parametrize("conflict", [
    "opencv-python", "opencv-contrib-python", "opencv-contrib-python-headless",
])
def test_opencv_provider_rejects_every_other_distribution(conflict):
    def package_version(name):
        if name == "opencv-python-headless":
            return "5.0.0.93"
        if name == conflict:
            return "5.0.0.93"
        raise PackageNotFoundError(name)

    message = f"conflicting OpenCV distributions are installed: {conflict}"
    with pytest.raises(SystemExit, match=message):
        _VERIFY_MODULE.verify_opencv_provider(
            package_version, lambda _: ["opencv-python>=4.7.0"]
        )


def _alias_package_version(name):
    versions = {
        "ultralytics": "8.4.177", "scenedetect": "0.7.2",
        "opencv-python-headless": "5.0.0.93",
    }
    if name not in versions:
        raise PackageNotFoundError(name)
    return versions[name]


def _alias_requirements(name):
    # Exact declarations audited from the hash-selected PyPI wheel metadata.
    return ["opencv-python" if name == "scenedetect"
            else "opencv-python!=4.13.0.90,>=4.7.0"]


def test_pip_check_allows_only_the_exact_opencv_alias_errors():
    expected = [
        "ultralytics 8.4.177 requires opencv-python, which is not installed.",
        "scenedetect 0.7.2 requires opencv-python, which is not installed.",
    ]

    def alias_only(_args, **_kwargs):
        return SimpleNamespace(returncode=1, stdout="\n".join(reversed(expected)), stderr="")

    assert _VERIFY_MODULE.verify_pip_check(
        alias_only, _alias_package_version, _alias_requirements
    ) == {
        "status": "documented_opencv_alias_only", "allowed_aliases": expected,
    }

    def clean(_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="No broken requirements found.\n", stderr="")

    assert _VERIFY_MODULE.verify_pip_check(clean, _alias_package_version) == {
        "status": "clean", "allowed_aliases": [],
    }

    def extra_error(_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=("\n".join(expected) + "\nother-package 1.0 requires missing-package, "
                    "which is not installed.\n"),
            stderr="",
        )

    with pytest.raises(SystemExit, match="unexpected dependency errors"):
        _VERIFY_MODULE.verify_pip_check(
            extra_error, _alias_package_version, _alias_requirements
        )


@pytest.mark.parametrize("consumer", ["ultralytics", "scenedetect"])
@pytest.mark.parametrize("declarations", [
    [], ["opencv-python<5"], ["opencv-python-headless>=4.7"],
    ["opencv-python>=4; python_version<'1'"],
    ["opencv-python>=4", "opencv-python>=5"],
    ["opencv-python @ https://example.invalid/opencv.whl"], ["not a requirement!"],
])
def test_opencv_alias_fails_closed_for_changed_declarations(consumer, declarations):
    def requirements(name):
        return declarations if name == consumer else _alias_requirements(name)

    with pytest.raises(SystemExit):
        _VERIFY_MODULE.verify_opencv_provider(_alias_package_version, requirements)


def test_ultralytics_cannot_drop_its_version_constraint():
    with pytest.raises(SystemExit, match="does not satisfy ultralytics"):
        _VERIFY_MODULE.verify_opencv_provider(_alias_package_version, lambda _: ["opencv-python"])


@pytest.mark.parametrize("returncode, lines", [
    (1, []), (1, ["No broken requirements found."]), (0, ["unexpected warning"]),
    (2, ["ultralytics 8.4.177 requires opencv-python, which is not installed."]),
    (1, ["ultralytics 8.4.177 requires opencv-python, which is not installed."]),
    (1, ["scenedetect 0.7.2 requires opencv-python, which is not installed."]),
    (1, ["ultralytics 8.4.177 requires opencv-python, which is not installed."] * 2),
])
def test_pip_check_rejects_unexpected_output(returncode, lines):
    def run(*args, **kwargs):
        return SimpleNamespace(returncode=returncode, stdout="\n".join(lines), stderr="")

    with pytest.raises(SystemExit, match="unexpected dependency errors"):
        _VERIFY_MODULE.verify_pip_check(run, _alias_package_version, _alias_requirements)


def test_pip_alias_errors_require_a_verified_provider():
    def run(*args, **kwargs):
        lines = [f"{name} {_alias_package_version(name)} requires opencv-python, "
                 "which is not installed." for name in ("ultralytics", "scenedetect")]
        return SimpleNamespace(returncode=1, stdout="\n".join(lines), stderr="")

    with pytest.raises(SystemExit):
        _VERIFY_MODULE.verify_pip_check(run, _alias_package_version, lambda _: [])


def test_verifier_changes_do_not_invalidate_dependency_install_layers():
    dockerfile = (PACKAGE / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.index('install -y --no-install-recommends') < dockerfile.index(
        'COPY requirements.lock'
    )
    assert dockerfile.index('COPY wheels/') < dockerfile.index('-m pip install')
    assert dockerfile.index('test "${stack_before}" = "${stack_after}"') < dockerfile.index(
        'COPY verify_runtime.py'
    )
    assert dockerfile.index('COPY verify_runtime.py') < dockerfile.index(
        'RUN set -eux;', dockerfile.index('COPY verify_runtime.py')
    )


def test_generated_wheels_are_ignored_without_ignoring_directory_marker():
    ignore_lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "infra/gx10-director/wheels/*.whl" in ignore_lines
    assert "infra/gx10-director/wheels/*" not in ignore_lines
    assert (PACKAGE / "wheels" / ".gitkeep").is_file()


def test_wheel_fetch_targets_only_cpython310_linux_aarch64():
    script = (PACKAGE / "fetch-wheels.sh").read_text(encoding="utf-8")

    for required in (
        "--platform manylinux_2_28_aarch64", "--python-version 3.10",
        "--platform manylinux2014_aarch64",
        "--implementation cp", "--abi cp310", "--only-binary=:all:",
        "--no-deps", "--require-hashes", "requirements.lock",
    ):
        assert required in script

    build = (PACKAGE / "build-image.sh").read_text(encoding="utf-8")
    assert "EXPECTED_BASE_IMAGE_ID" in build
    assert "docker image inspect --format '{{.Id}}'" in build
    assert "--platform linux/arm64" in build
    assert "{{.Config.User}}" in build
    assert "{{.Architecture}}" in build
    assert 'python_executable=${PYTHON:-/usr/bin/python3}' in build
    assert '--build-arg "PYTHON=$python_executable"' in build


def test_packaging_docs_distinguish_candidate_from_target_runtime_proof():
    docs = (ROOT / "docs" / "gx10-node-packaging.md").read_text(encoding="utf-8")

    assert "2.11.0" in docs
    assert "stable ABI" in docs
    assert "ffprobe" in docs
    assert "GX10 runtime" in docs
