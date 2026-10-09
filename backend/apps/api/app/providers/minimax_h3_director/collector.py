"""Resolve exported artifacts through a frozen, qualified exporter manifest."""

import hashlib
from dataclasses import dataclass
from typing import Any

from apps.api.app.integrations.comfy_adapter import ComfyAdapter

from .contracts import artifact_member_range


@dataclass(frozen=True)
class DirectorArtifact:
    role: str
    ordinal: int
    member_index: int | None
    locator: dict[str, Any]
    expected: dict[str, Any]


def artifact_manifest(
    history: dict, profile: dict, *, require_final: bool = True, member_count: int | None = None
) -> list[DirectorArtifact]:
    """Node identities come from qualification, never filenames or report prose."""
    bindings = profile.get("output_artifacts")
    if bindings is None:
        output_node = profile.get("output_node")
        if not isinstance(output_node, str) or not output_node:
            raise ValueError("DIRECTOR_ARTIFACT_MANIFEST_REQUIRED")
        bindings = [{"role": "final", "node_id": output_node, "required": True}]
    if not isinstance(bindings, list) or not bindings or len(bindings) > 128:
        raise ValueError("DIRECTOR_ARTIFACT_MANIFEST_INVALID")
    artifacts, identities, locators = [], set(), set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("DIRECTOR_ARTIFACT_BINDING_INVALID")
        role, node_id = binding.get("role"), binding.get("node_id")
        if role == "report":
            continue
        if role not in {"final", "segment", "pre-refine", "pre-face"}:
            raise ValueError("DIRECTOR_ARTIFACT_ROLE_INVALID")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("DIRECTOR_ARTIFACT_EXPORTER_MISSING")
        coverage = artifact_member_range(binding, member_count)
        if binding.get("transport") == "studio_native_segments_v1":
            records = history.get("outputs", {}).get(node_id, {}).get("studio_director_artifacts")
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise ValueError("DIRECTOR_NATIVE_MANIFEST_REQUIRED")
            selected = [item for item in records if item.get("role") == role]
            if any(type(item.get("member_index")) is not int for item in selected):
                raise ValueError("DIRECTOR_ARTIFACT_MEMBER_INVALID")
            selected.sort(key=lambda item: item.get("member_index", -1))
            if [item.get("member_index") for item in selected] != list(coverage):
                if selected or binding.get("required", True):
                    raise ValueError("DIRECTOR_NATIVE_MEMBER_COVERAGE_INVALID")
            outputs = ComfyAdapter.outputs({"outputs": {node_id: {"videos": selected}}}, node_id)
        else:
            outputs = ComfyAdapter.outputs(history, node_id)
        if any(output["kind"] != "videos" for output in outputs):
            raise ValueError("DIRECTOR_ARTIFACT_NOT_VIDEO")
        expected_count = len(coverage)
        if "coverage" not in binding and expected_count > 100:
            raise ValueError("DIRECTOR_ARTIFACT_COUNT_INVALID")
        if not outputs and binding.get("required", True) is False:
            continue
        if len(outputs) != expected_count:
            raise ValueError("DIRECTOR_ARTIFACT_COUNT_MISMATCH")
        member_start = coverage.start if "coverage" in binding else binding.get("member_index")
        if member_start is not None and (type(member_start) is not int or member_start < 0):
            raise ValueError("DIRECTOR_ARTIFACT_MEMBER_INVALID")
        if role == "segment" and member_start is None:
            raise ValueError("DIRECTOR_SEGMENT_MEMBER_REQUIRED")
        for ordinal, output in enumerate(outputs):
            member_index = member_start + ordinal if member_start is not None else None
            identity = (role, member_index, ordinal)
            locator = (output["filename"], output["subfolder"], output["type"])
            if identity in identities or locator in locators:
                raise ValueError("DIRECTOR_ARTIFACT_DUPLICATE")
            identities.add(identity)
            locators.add(locator)
            artifacts.append(
                DirectorArtifact(
                    role, ordinal, member_index, output, dict(binding.get("expected") or {})
                )
            )
    final_count = sum(artifact.role == "final" for artifact in artifacts)
    if final_count > 1 or (require_final and final_count != 1):
        raise ValueError("DIRECTOR_FINAL_ARTIFACT_AMBIGUOUS")
    return artifacts


def report_artifacts(history: dict, profile: dict) -> list[dict[str, Any]]:
    reports = []
    for binding in profile.get("output_artifacts", []):
        if binding.get("role") != "report":
            continue
        node_id = binding.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("DIRECTOR_REPORT_NODE_REQUIRED")
        payload = history.get("outputs", {}).get(node_id, {}).get("text")
        if payload is None and not binding.get("required", False):
            continue
        if isinstance(payload, str):
            payload = [payload]
        if not isinstance(payload, list) or not all(isinstance(part, str) for part in payload):
            raise ValueError("DIRECTOR_REPORT_INVALID")
        text = "\n".join(payload)
        encoded = text.encode("utf-8")
        if len(encoded) > 64 * 1024:
            raise ValueError("DIRECTOR_REPORT_TOO_LARGE")
        reports.append(
            {
                "role": "report",
                "provider_locator": {"node_id": node_id},
                "text": text,
                "checksum": hashlib.sha256(encoded).hexdigest(),
                "size_bytes": len(encoded),
            }
        )
    return reports


def validate_artifact(metadata: dict, expected: dict) -> None:
    if not metadata.get("has_video"):
        raise ValueError("DIRECTOR_ARTIFACT_VIDEO_MISSING")
    for field in ("width", "height", "frames", "has_audio"):
        if field in expected and metadata.get(field) != expected[field]:
            raise ValueError(f"DIRECTOR_ARTIFACT_{field.upper()}_MISMATCH")
    if "fps" in expected:
        fps = metadata.get("fps")
        if fps is None or abs(fps - expected["fps"]) > 0.01:
            raise ValueError("DIRECTOR_ARTIFACT_FPS_MISMATCH")
