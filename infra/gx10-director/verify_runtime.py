"""Build-time CPU smoke test for the pinned ARM64 Director dependency image."""
from __future__ import annotations

import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from importlib import import_module, metadata
from pathlib import Path

from packaging.version import Version

ROOT = Path(__file__).resolve().parent
BASE_RUNTIME_PACKAGES = ("scipy", "safetensors", "aiohttp")
DIRECTOR_IMPORTS = ("PIL", "cv2", "imageio_ffmpeg", "scenedetect", "numpy")
OPENCV_ALIAS_CONSUMERS = ("ultralytics", "scenedetect")
CONFLICTING_OPENCV_DISTRIBUTIONS = (
    "opencv-python", "opencv-contrib-python", "opencv-contrib-python-headless",
)


def locked_packages() -> dict[str, str]:
    requirements = {}
    for line in (ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\\ ]+) \\", line)
        if match:
            requirements[match.group(1)] = match.group(2)
    return requirements


def verify_base_runtime_packages(metadata_version=metadata.version) -> dict[str, str]:
    versions = {}
    for name in BASE_RUNTIME_PACKAGES:
        try:
            versions[name] = metadata_version(name)
        except Exception as exc:
            raise SystemExit(f"missing ComfyUI base dependency: {name}") from exc
    return versions


def verify_base_runtime_imports(importer=import_module) -> None:
    for module_name in BASE_RUNTIME_PACKAGES + ("PIL",):
        try:
            importer(module_name)
        except Exception as exc:
            raise SystemExit(f"ComfyUI base dependency cannot be imported: {module_name}") from exc


def verify_director_imports(importer=import_module) -> tuple[str, ...]:
    for module_name in DIRECTOR_IMPORTS:
        try:
            importer(module_name)
        except Exception as exc:
            raise SystemExit(f"Director dependency cannot be imported: {module_name}") from exc

    previous_config = os.environ.get("YOLO_CONFIG_DIR")
    try:
        with tempfile.TemporaryDirectory(prefix="gx10-ultralytics-config-") as config_dir:
            os.environ["YOLO_CONFIG_DIR"] = config_dir
            try:
                importer("ultralytics")
            except Exception as exc:
                raise SystemExit("Director dependency cannot be imported: ultralytics") from exc
    finally:
        if previous_config is None:
            os.environ.pop("YOLO_CONFIG_DIR", None)
        else:
            os.environ["YOLO_CONFIG_DIR"] = previous_config
    return DIRECTOR_IMPORTS + ("ultralytics",)


def verify_opencv_provider(metadata_version=metadata.version,
                           metadata_requires=metadata.requires) -> dict:
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name

    try:
        headless_version = Version(metadata_version("opencv-python-headless"))
    except Exception as exc:
        raise SystemExit("missing OpenCV headless provider") from exc
    for distribution in CONFLICTING_OPENCV_DISTRIBUTIONS:
        try:
            metadata_version(distribution)
        except metadata.PackageNotFoundError:
            continue
        raise SystemExit(f"conflicting OpenCV distributions are installed: {distribution}")

    satisfied = {}
    for consumer in OPENCV_ALIAS_CONSUMERS:
        requirements = []
        try:
            for raw_requirement in metadata_requires(consumer) or []:
                requirement = Requirement(raw_requirement)
                if canonicalize_name(requirement.name) == "opencv-python" and (
                    requirement.marker is None or requirement.marker.evaluate()
                ):
                    requirements.append(requirement)
        except (InvalidRequirement, metadata.PackageNotFoundError) as exc:
            raise SystemExit(f"invalid OpenCV dependency metadata: {consumer}") from exc
        if len(requirements) != 1:
            raise SystemExit(f"expected one active OpenCV dependency: {consumer}")
        requirement = requirements[0]
        if (requirement.url or requirement.extras
                or (not requirement.specifier and consumer != "scenedetect")
                or not requirement.specifier.contains(headless_version, prereleases=True)):
            raise SystemExit(f"OpenCV headless provider does not satisfy {consumer}")
        satisfied[consumer] = str(requirement)
    return {"distribution": "opencv-python-headless", "version": str(headless_version),
            "satisfies": satisfied}


def verify_pip_check(
    run=subprocess.run, installed_version=metadata.version,
    metadata_requires=metadata.requires,
) -> dict:
    result = run([sys.executable, "-m", "pip", "check"],
                 capture_output=True, text=True, check=False)
    lines = [line.strip() for line in (result.stdout + "\n" + result.stderr).splitlines()
             if line.strip()]
    if result.returncode == 0 and lines in ([], ["No broken requirements found."]):
        return {"status": "clean", "allowed_aliases": []}

    expected_aliases = [
        f"{consumer} {installed_version(consumer)} requires opencv-python, "
        "which is not installed." for consumer in OPENCV_ALIAS_CONSUMERS
    ]
    if (result.returncode == 1 and len(lines) == len(expected_aliases)
            and set(lines) == set(expected_aliases)):
        # Never waive metadata errors without revalidating the actual provider.
        verify_opencv_provider(installed_version, metadata_requires)
        return {"status": "documented_opencv_alias_only", "allowed_aliases": expected_aliases}
    raise SystemExit(f"pip check found unexpected dependency errors: {lines!r}")


def main() -> None:
    if (platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 10)
            or platform.system() != "Linux"
            or platform.machine().lower() not in {"aarch64", "arm64"}):
        raise SystemExit("expected CPython 3.10 on Linux aarch64")

    expected = {
        "torch": os.environ["EXPECTED_TORCH"],
        "torchvision": os.environ["EXPECTED_TORCHVISION"],
        "torchaudio": "2.11.0",
        "numpy": "2.2.6",
        "opencv-python-headless": "5.0.0.93",
        "imageio-ffmpeg": "0.6.0",
        "scenedetect": "0.7.2",
        "ultralytics": "8.4.177",
    }
    locked = locked_packages()
    versions = {name: metadata.version(name) for name in locked}
    for name, version in versions.items():
        if version != locked[name]:
            raise SystemExit(f"locked package version mismatch: {name}={version}")
    for name, version in expected.items():
        installed_version = metadata.version(name)
        if name in {"torch", "torchvision"}:
            # NVIDIA metadata may omit the CUDA local suffix. Full imported
            # runtime versions and the CUDA build are checked separately below.
            matches = Version(installed_version).public == Version(version).public
        else:
            matches = installed_version == version
        if not matches:
            raise SystemExit(f"runtime package version mismatch: {name}")
    base_versions = verify_base_runtime_packages()
    opencv_provider = verify_opencv_provider()

    import cv2
    import torch
    import torchaudio
    import torchvision

    verify_base_runtime_imports()
    director_imports = verify_director_imports()

    if torch.__version__ != expected["torch"] or torchvision.__version__ != expected["torchvision"]:
        raise SystemExit("the pre-existing torch/torchvision stack changed")
    if str(torch.version.cuda) != os.environ["EXPECTED_CUDA"]:
        raise SystemExit("the pre-existing CUDA build changed")

    # Exercise the exact functional resampler used by the pinned Director. CPU only.
    source_rate = 32000
    target_rate = 44100
    source_samples = 3200
    waveform_values = [0.5 * math.sin(2 * math.pi * 1000 * index / source_rate)
                       for index in range(source_samples)]
    waveform = torch.tensor(waveform_values, dtype=torch.float32).reshape(1, 1, source_samples)
    resampled = torchaudio.functional.resample(waveform, source_rate, target_rate)
    if tuple(resampled.shape) != (1, 1, 4410) or not torch.isfinite(resampled).all().item():
        raise SystemExit("torchaudio functional resample smoke test failed")
    values = resampled.detach().cpu().reshape(-1).tolist()
    expected_signal = [0.5 * math.sin(2 * math.pi * 1000 * index / target_rate)
                       for index in range(len(values))]
    if not all(math.isfinite(value) for value in values):
        raise SystemExit("torchaudio functional resample smoke test failed")
    observed_power = math.fsum(value * value for value in values)
    expected_power = math.fsum(value * value for value in expected_signal)
    rms = math.sqrt(observed_power / len(values))
    similarity = math.fsum(
        value * reference for value, reference in zip(values, expected_signal, strict=True)
    )
    similarity /= math.sqrt(observed_power * expected_power)
    if not 0.25 <= rms <= 0.40 or similarity < 0.95:
        raise SystemExit("torchaudio resample did not preserve the non-zero 1 kHz signal")
    if not cv2.__version__.startswith("5.0.0"):
        raise SystemExit("OpenCV import/version mismatch")

    binaries = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if path is None:
            raise SystemExit(f"missing executable: {name}")
        result = subprocess.run([path, "-version"], capture_output=True, text=True, check=True)
        first_line = result.stdout.splitlines()[0]
        if "4.4.2" not in first_line:
            raise SystemExit(f"unexpected {name} version: {first_line}")
        binaries[name] = first_line

    pip_check = verify_pip_check()
    print(json.dumps({"python": sys.version.split()[0], "machine": platform.machine(),
                      "packages": versions, "torch_cuda": torch.version.cuda,
                      "base_packages": base_versions,
                      "opencv_provider": opencv_provider,
                      "director_imports": director_imports, "pip_check": pip_check,
                      "resample": {"input_samples": source_samples, "output_samples": 4410,
                                   "rms": rms, "signal_similarity": similarity},
                      "binaries": binaries}, sort_keys=True))


if __name__ == "__main__":
    main()
