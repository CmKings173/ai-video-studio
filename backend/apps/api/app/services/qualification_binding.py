"""Shared immutable provenance checks for single and aggregate executed cases."""

import re
from typing import Any

from .workflow_registry import _stable_hash

PROVENANCE_KEYS = (
    "workflow_hash",
    "slot_map_hash",
    "model_hash",
    "lora_hashes",
    "comfyui_commit",
    "custom_node_versions",
    "profile_hash",
    "weight_hashes",
    "dependency_versions",
)


def execution_profile_hash(profile: dict) -> str:
    return _stable_hash(
        {
            key: value
            for key, value in profile.items()
            if key not in {"execution_evidence", "poc_verified", "benchmark_evidence"}
        }
    )


def provenance_matches(profile: dict[str, Any], case: dict[str, Any]) -> bool:
    baseline = profile.get("execution_evidence") or {}
    if not isinstance(baseline, dict) or any(
        key not in baseline or key not in case for key in PROVENANCE_KEYS
    ):
        return False
    if baseline["profile_hash"] != execution_profile_hash(profile):
        return False
    weights, dependencies = profile.get("weight_hashes"), profile.get("dependency_versions")
    if not isinstance(weights, dict) or not isinstance(dependencies, dict):
        return False
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(digest, str)
        or re.fullmatch(r"[a-f0-9]{64}", digest) is None
        for name, digest in weights.items()
    ):
        return False
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(version, str)
        or not version.strip()
        for name, version in dependencies.items()
    ):
        return False
    if baseline["weight_hashes"] != weights or baseline["dependency_versions"] != dependencies:
        return False
    return all(case[key] == baseline[key] for key in PROVENANCE_KEYS)


def optional_weights_bound(profile: dict[str, Any], settings: dict[str, Any]) -> bool:
    face = settings.get("face_refine") or {}
    return not face.get("enabled") or face.get("detector") in profile.get("weight_hashes", {})
