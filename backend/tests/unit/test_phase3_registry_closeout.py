from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.services.workflow_contracts import profile_hash, require_contract
from apps.api.app.services.workflow_loader import load_manifests
from apps.api.app.services.workflow_qualification import qualification_status
from apps.api.app.services.workflow_registry import ApprovedWorkflow

WORKFLOW_DIRECTORY = Path(__file__).resolve().parents[2] / "workflows" / "h3"


def _manifest_directory(tmp_path: Path, *, count: int = 1) -> Path:
    entries = json.loads((WORKFLOW_DIRECTORY / "registry.json").read_text(encoding="utf-8"))[
        "workflows"
    ][:count]
    for entry in entries:
        graph_path = WORKFLOW_DIRECTORY / entry["file"]
        (tmp_path / entry["file"]).write_bytes(graph_path.read_bytes())
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema_version": 1, "workflows": entries}), encoding="utf-8"
    )
    return tmp_path


def _rewrite_registry(directory: Path, mutate) -> None:
    registry_path = directory / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    mutate(registry["workflows"])
    registry_path.write_text(json.dumps(registry), encoding="utf-8")


def test_all_h3_templates_load_with_exact_graph_and_slot_hashes():
    entries = load_manifests(WORKFLOW_DIRECTORY)

    assert len(entries) == 12
    assert len({(entry["code"], entry["version"]) for entry in entries}) == 12
    for entry in entries:
        approved = ApprovedWorkflow(
            mode=entry["mode"],
            version=entry["version"],
            workflow=entry["workflow"],
            slots={key: tuple(binding) for key, binding in entry["slots"].items()},
            required_slots=frozenset(entry["required_slots"]),
            execution_scope=entry["execution_scope"],
        )
        assert entry["profile"]["workflow_hash"] == approved.workflow_hash
        assert entry["profile"]["slot_map_hash"] == approved.slot_map_hash
        status = qualification_status(
            entry["profile"], approved.workflow_hash, approved.slot_map_hash
        )
        assert status.qualified is False


@pytest.mark.parametrize(
    "damage",
    [
        "workflow_hash",
        "workflow_hash_missing",
        "slot_map_hash",
        "slot_map_hash_missing",
        "scope",
        "source_revision",
        "output_manifest",
        "workflow_profile",
    ],
)
def test_loader_rejects_manifest_contract_mismatches(tmp_path, damage):
    directory = _manifest_directory(tmp_path)

    def mutate(entries):
        profile = entries[0]["profile"]
        if damage == "workflow_hash":
            profile["workflow_hash"] = "0" * 64
        elif damage == "workflow_hash_missing":
            profile.pop("workflow_hash")
        elif damage == "slot_map_hash":
            profile["slot_map_hash"] = "0" * 64
        elif damage == "slot_map_hash_missing":
            profile.pop("slot_map_hash")
        elif damage == "scope":
            profile["execution_scope"] = "aggregate"
        elif damage == "source_revision":
            profile["provider_source"]["commit"] = "0" * 40
        elif damage == "output_manifest":
            profile["output_artifacts"][0]["node_id"] = "missing-node"
        elif damage == "workflow_profile":
            profile["fps"] = 30

    _rewrite_registry(directory, mutate)

    with pytest.raises((AppError, ValueError)):
        load_manifests(directory)


def test_loader_rejects_duplicate_code_version_identity(tmp_path):
    directory = _manifest_directory(tmp_path)

    def duplicate(entries):
        entries.append(copy.deepcopy(entries[0]))

    _rewrite_registry(directory, duplicate)

    with pytest.raises(ValueError, match="code/version"):
        load_manifests(directory)


def test_retired_workflow_cannot_pass_runtime_contract_gate():
    record = SimpleNamespace(profile={"retired": True})

    with pytest.raises(AppError) as error:
        require_contract(record, None)

    assert error.value.code == "WORKFLOW_RETIRED"


def _runtime_qualification_profile() -> dict:
    studio_revision = "1" * 40
    comfyui_version = "0.3.50"
    return {
        "poc_verified": True,
        "model_hash": "c" * 64,
        "lora_hashes": {},
        "comfyui_commit": "d" * 40,
        "studio_revision": studio_revision,
        "comfyui_version": comfyui_version,
        "custom_node_versions": {"director-node": "1.2.3"},
        "provider_source": {
            "repository": "AIMixer/ComfyUI_MiniMaxH3_Director",
            "commit": "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb",
        },
        "execution_spec_hash": "2" * 64,
        "execution_evidence": {
            "executed": True,
            "workflow_hash": "a" * 64,
            "slot_map_hash": "b" * 64,
            "tested_at": "2026-10-05T00:00:00Z",
            "prompt_id": "runtime-prompt-1",
            "model_hash": "c" * 64,
            "lora_hashes": {},
            "comfyui_commit": "d" * 40,
            "custom_node_versions": {"director-node": "1.2.3"},
            "studio_revision": studio_revision,
            "director_repository": "AIMixer/ComfyUI_MiniMaxH3_Director",
            "director_commit": "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb",
            "comfyui_version": comfyui_version,
            "execution_spec_hash": "2" * 64,
            "gpu_model": "NVIDIA RTX 6000 Ada",
            "gpu_count": 1,
            "driver_version": "550.90.07",
            "cuda_runtime": "12.4",
            "output": {
                "width": 864,
                "height": 480,
                "fps": 24,
                "duration_seconds": 5.0,
                "frame_count": 120,
                "video_codec": "h264",
                "container": "mp4",
                "has_video": True,
                "has_audio": True,
                "audio_sample_rate": 48000,
                "audio_channels": 2,
                "audio_codec": "aac",
                "checksum": "f" * 64,
                "size_bytes": 1000,
            },
        },
    }


def test_complete_pinned_runtime_evidence_is_qualified():
    status = qualification_status(_runtime_qualification_profile(), "a" * 64, "b" * 64)

    assert status.qualified is True
    assert status.lifecycle == "POC_EXECUTED"


def test_runtime_qualification_rejects_valid_hash_for_a_different_execution_spec():
    profile = _runtime_qualification_profile()
    profile["execution_spec_hash"] = "3" * 64
    profile["execution_evidence"]["profile_hash"] = profile_hash(profile)

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False
    assert status.lifecycle == "STATIC_VALIDATED"


@pytest.mark.parametrize("target_hash", [None, "malformed"])
def test_runtime_qualification_rejects_missing_or_malformed_execution_spec_pin(target_hash):
    profile = _runtime_qualification_profile()
    if target_hash is None:
        profile.pop("execution_spec_hash")
    else:
        profile["execution_spec_hash"] = target_hash
    profile["execution_evidence"]["profile_hash"] = profile_hash(profile)

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False
    assert status.lifecycle == "STATIC_VALIDATED"


@pytest.mark.parametrize(
    "field_path",
    [
        ("studio_revision",),
        ("director_repository",),
        ("director_commit",),
        ("comfyui_version",),
        ("execution_spec_hash",),
        ("gpu_model",),
        ("gpu_count",),
        ("driver_version",),
        ("cuda_runtime",),
        ("output", "frame_count"),
        ("output", "video_codec"),
        ("output", "container"),
        ("output", "audio_sample_rate"),
        ("output", "audio_channels"),
        ("output", "audio_codec"),
    ],
)
def test_runtime_qualification_rejects_missing_release_identity_and_measurements(field_path):
    profile = _runtime_qualification_profile()
    value = profile["execution_evidence"]
    for part in field_path[:-1]:
        value = value[part]
    value.pop(field_path[-1])

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False
    assert status.lifecycle == "STATIC_VALIDATED"


def test_legacy_minimal_runtime_evidence_is_not_promoted_to_poc_executed():
    profile = _runtime_qualification_profile()
    evidence = profile["execution_evidence"]
    for field in (
        "studio_revision",
        "director_repository",
        "director_commit",
        "comfyui_version",
        "execution_spec_hash",
        "gpu_model",
        "gpu_count",
        "driver_version",
        "cuda_runtime",
    ):
        evidence.pop(field)
    for field in (
        "frame_count",
        "video_codec",
        "container",
        "audio_sample_rate",
        "audio_channels",
        "audio_codec",
    ):
        evidence["output"].pop(field)

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False
    assert status.lifecycle == "STATIC_VALIDATED"


@pytest.mark.parametrize(
    ("field_path", "invalid_value"),
    [
        (("studio_revision",), "not-a-revision"),
        (("director_repository",), " "),
        (("director_commit",), "not-a-commit"),
        (("comfyui_version",), ""),
        (("execution_spec_hash",), "not-a-hash"),
        (("gpu_model",), " "),
        (("gpu_count",), 0),
        (("gpu_count",), True),
        (("driver_version",), ""),
        (("cuda_runtime",), " "),
        (("output", "frame_count"), 0),
        (("output", "video_codec"), ""),
        (("output", "container"), " "),
        (("output", "audio_sample_rate"), 0),
        (("output", "audio_channels"), 0),
        (("output", "audio_codec"), ""),
    ],
)
def test_runtime_qualification_rejects_malformed_identity_and_measurements(
    field_path, invalid_value
):
    profile = _runtime_qualification_profile()
    value = profile["execution_evidence"]
    for part in field_path[:-1]:
        value = value[part]
    value[field_path[-1]] = invalid_value

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False


@pytest.mark.parametrize(
    ("field", "profile_field", "invalid_value"),
    [
        ("studio_revision", "studio_revision", "2" * 40),
        ("comfyui_version", "comfyui_version", "0.3.51"),
        ("director_repository", None, "other/Director"),
        ("director_commit", None, "b" * 40),
    ],
)
def test_runtime_qualification_rejects_release_identity_mismatch(
    field, profile_field, invalid_value
):
    profile = _runtime_qualification_profile()
    if profile_field is None:
        source_field = "repository" if field == "director_repository" else "commit"
        profile["provider_source"][source_field] = invalid_value
    else:
        profile[profile_field] = invalid_value

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False


@pytest.mark.parametrize(
    ("profile_field", "source_field"),
    [
        ("studio_revision", None),
        ("comfyui_version", None),
        ("provider_source", "repository"),
        ("provider_source", "commit"),
    ],
)
def test_runtime_qualification_requires_declared_release_identity_pins(profile_field, source_field):
    profile = _runtime_qualification_profile()
    if source_field is None:
        profile.pop(profile_field)
    else:
        profile[profile_field].pop(source_field)

    status = qualification_status(profile, "a" * 64, "b" * 64)

    assert status.qualified is False
    assert status.lifecycle == "STATIC_VALIDATED"
