"""Pinned source-only installer. Never installs dependencies or downloads weights."""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import stat
import tempfile
import urllib.request
import zipfile
from importlib.metadata import version
from pathlib import Path, PurePosixPath

BASE = Path(__file__).resolve().parent
NODES = (
    "MiniMaxH3Director", "MiniMaxH3DirectorRefine",
    "MiniMaxH3DirectorFaceRefine", "StudioMiniMaxH3Director",
)
# Inspected from this pin's INPUT_TYPES; COMBO means a list of runtime choices.
DIRECTOR_REQUIRED = {
    "model": "MODEL", "video_vae": "VAE", "audio_vae": "VAE", "clip": "CLIP",
}
DIRECTOR_OPTIONAL = {"refine": "MMX_DIR_REFINE", "face_refine": "MMX_DIR_FACE_REFINE"}
INPUT_CONTRACTS = {
    "MiniMaxH3Director": (DIRECTOR_REQUIRED, DIRECTOR_OPTIONAL),
    "StudioMiniMaxH3Director": (
        DIRECTOR_REQUIRED, {**DIRECTOR_OPTIONAL, "studio_output_prefix": "STRING"},
    ),
    "MiniMaxH3DirectorRefine": (
        {"mode": "COMBO", "upscale_method": "COMBO", "latent_upscale_model": "COMBO",
         "sampler": "COMBO", "passes": "INT"},
        {"refine_model": "MODEL", "sigmas": "SIGMAS"},
    ),
    "MiniMaxH3DirectorFaceRefine": (
        {"bd_grp_face_detect": "BDGROUP", "detector": "COMBO", "confidence": "FLOAT",
         "crop_factor": "FLOAT", "canvas_width": "INT", "canvas_height": "INT",
         "canvas_mode": "COMBO", "select": "COMBO", "bd_grp_face_sample": "BDGROUP",
         "denoise": "FLOAT", "steps": "INT", "sampler": "COMBO", "scheduler": "COMBO"},
        {"sigmas": "SIGMAS"},
    ),
}


def load_manifest():
    return json.loads((BASE / "gx10_manifest.json").read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_tree(root, expected):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("symlink package root")
    actual = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"symlink source: {path}")
        if path.is_file():
            # Python runtime caches are not deployment inputs.
            if "__pycache__" in path.relative_to(root).parts:
                continue
            actual[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    if actual.keys() != expected.keys():
        raise ValueError("source inventory mismatch")
    for name, checksum in expected.items():
        if actual[name] != checksum:
            raise ValueError(f"source integrity mismatch: {name}")


def unpack(data, destination, manifest):
    if digest(data) != manifest["archive_sha256"]:
        raise ValueError("archive integrity mismatch")
    prefix = f"ComfyUI_MiniMaxH3_Director-{manifest['commit']}/"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        seen = set()
        for entry in archive.infolist():
            name = entry.filename
            if not name.startswith(prefix) or "\\" in name:
                raise ValueError("unsafe archive path")
            relative = name[len(prefix):]
            if entry.is_dir():
                continue
            path = PurePosixPath(relative)
            if (path.is_absolute() or ".." in path.parts or relative not in manifest["source"]
                    or relative in seen or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError("unsafe archive member")
            seen.add(relative)
            content = archive.read(entry)
            if digest(content) != manifest["source"][relative]:
                raise ValueError("member integrity mismatch")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    verify_tree(destination, manifest["source"])


def check_dependencies(requirements, get_version=version):
    from packaging.requirements import Requirement

    result = {}
    # Includes lazy FaceRefine dependencies; optional NVIDIA VFX is not installed.
    for line in requirements.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        try:
            installed = get_version(req.name)
        except Exception as exc:
            raise ValueError(f"missing dependency: {req.name}") from exc
        if not req.specifier.contains(installed, prereleases=True):
            raise ValueError(f"incompatible dependency: {req}: {installed}")
        result[req.name] = installed
    return result


def validate_nodes(info):
    if not isinstance(info, dict):
        raise ValueError("node inventory must be a mapping")
    contracts = load_manifest()["outputs"]
    for name in NODES:
        if name not in info:
            raise ValueError(f"missing node: {name}")
        node = info[name]
        if not isinstance(node, dict) or node.get("output") != contracts[name]:
            raise ValueError(f"node contract mismatch: {name}")
        inputs = node.get("input")
        if not isinstance(inputs, dict):
            raise ValueError(f"node input contract mismatch: {name}")
        for section, expected in zip(("required", "optional"), INPUT_CONTRACTS[name], strict=True):
            fields = inputs.get(section)
            if not isinstance(fields, dict):
                raise ValueError(f"node schema contract mismatch: {name}.{section}")
            for field, descriptor in fields.items():
                if (not isinstance(field, str) or not field
                        or not isinstance(descriptor, (list, tuple))
                        or not 1 <= len(descriptor) <= 2
                        or (len(descriptor) == 2 and not isinstance(descriptor[1], dict))):
                    raise ValueError(f"node descriptor contract mismatch: {name}.{field}")
                kind = descriptor[0]
                if not ((isinstance(kind, str) and kind)
                        or (isinstance(kind, list) and all(
                            isinstance(choice, (str, int, float, bool)) for choice in kind
                        ))):
                    raise ValueError(f"node descriptor contract mismatch: {name}.{field}")
            for field, kind in expected.items():
                if field not in fields:
                    raise ValueError(f"node socket contract mismatch: {name}.{section}.{field}")
                actual = fields[field][0]
                matches = isinstance(actual, list) if kind == "COMBO" else actual == kind
                if not matches:
                    raise ValueError(f"node socket contract mismatch: {name}.{section}.{field}")
        if inputs["required"].keys() & inputs["optional"].keys():
            raise ValueError(f"duplicate node socket contract: {name}")


def verify_installed(comfy_root, info):
    """Recheck disk integrity alongside the target's saved live node inventory."""
    manifest = load_manifest()
    custom = Path(comfy_root) / "custom_nodes"
    verify_tree(custom / "ComfyUI_MiniMaxH3_Director", manifest["source"])
    verify_tree(custom / "studio_director_bridge", manifest["bridge"])
    requirements = (custom / "ComfyUI_MiniMaxH3_Director" / "requirements.txt").read_text()
    dependencies = check_dependencies(requirements)
    dependencies.update(check_dependencies(
        "torch\ntorchaudio\nnumpy\nPillow\nscipy\nsafetensors\naiohttp\npackaging"
    ))
    validate_nodes(info)
    return {"commit": manifest["commit"], "dependencies": dependencies,
            "qualification": "UNQUALIFIED: H3 runtime evidence still required"}


def install(comfy_root, archive_path=None):
    manifest = load_manifest()
    root = Path(comfy_root).resolve(strict=True)
    if not (root / "main.py").is_file() or not (root / "comfy").is_dir():
        raise ValueError("expected an existing ComfyUI source root")
    custom = root / "custom_nodes"
    if not custom.is_dir() or custom.is_symlink():
        raise ValueError("expected real custom_nodes directory")
    targets = [custom / "ComfyUI_MiniMaxH3_Director", custom / "studio_director_bridge"]
    if any(p.exists() or p.is_symlink() for p in targets):
        raise ValueError("destination exists; refusing to overwrite")
    verify_tree(BASE / "studio_director_bridge", manifest["bridge"])
    data = Path(archive_path).read_bytes() if archive_path else urllib.request.urlopen(
        manifest["url"], timeout=60
    ).read()
    with tempfile.TemporaryDirectory(prefix="gx10-source-") as temporary:
        source = Path(temporary) / "director"
        source.mkdir()
        unpack(data, source, manifest)
        for path in source.rglob("*.py"):
            ast.parse(path.read_bytes(), filename=str(path))
        dependencies = check_dependencies((source / "requirements.txt").read_text())
        dependencies.update(check_dependencies(
            "torch\ntorchaudio\nnumpy\nPillow\nscipy\nsafetensors\naiohttp\npackaging"
        ))
        # Copy only verified bytes, never caches or local extras. No package hooks run.
        for src, dst, inventory in (
            (source, targets[0], manifest["source"]),
            (BASE / "studio_director_bridge", targets[1], manifest["bridge"]),
        ):
            dst.mkdir()  # exclusive; concurrent or existing installs fail closed
            for name in inventory:
                content = (src / name).read_bytes()
                if digest(content) != inventory[name]:
                    raise ValueError(f"source integrity mismatch: {name}")
                target = dst / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            verify_tree(dst, inventory)
    return {"commit": manifest["commit"], "dependencies": dependencies,
            "qualification": "UNQUALIFIED: validate object_info; H3 gates still required"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    deploy = sub.add_parser("install")
    deploy.add_argument("--comfy-root", required=True)
    deploy.add_argument("--archive", help="offline official ZIP; checksum still required")
    check = sub.add_parser("validate-nodes")
    check.add_argument("--object-info", required=True, help="saved /object_info JSON from target")
    verify = sub.add_parser("verify-installed")
    verify.add_argument("--comfy-root", required=True)
    verify.add_argument("--object-info", required=True)
    args = parser.parse_args()
    if args.command == "install":
        print(json.dumps(install(args.comfy_root, args.archive), indent=2))
    elif args.command == "validate-nodes":
        validate_nodes(json.loads(Path(args.object_info).read_text(encoding="utf-8")))
        print("Four node contracts valid; runtime generation remains unqualified")
    else:
        info = json.loads(Path(args.object_info).read_text(encoding="utf-8"))
        print(json.dumps(verify_installed(args.comfy_root, info), indent=2))


if __name__ == "__main__":
    main()
