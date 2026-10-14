import copy

import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.services.workflow_qualification import qualification_status, require_qualified


def evidence_profile():
    return {
        "poc_verified": True,
        "studio_revision": "1" * 40,
        "comfyui_version": "0.3.50-test",
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
            "prompt_id": "test-only-prompt",
            "model_hash": "c" * 64,
            "lora_hashes": {},
            "comfyui_commit": "d" * 40,
            "studio_revision": "1" * 40,
            "director_repository": "AIMixer/ComfyUI_MiniMaxH3_Director",
            "director_commit": "a8f57b8e23c46ee28fdb96796510fa7b4f3b1fbb",
            "comfyui_version": "0.3.50-test",
            "execution_spec_hash": "2" * 64,
            "gpu_model": "synthetic-test-only",
            "gpu_count": 1,
            "driver_version": "synthetic-test-only",
            "cuda_runtime": "synthetic-test-only",
            "custom_node_versions": {"test-fixture-node": "e" * 40},
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


@pytest.mark.parametrize(
    "profile", [{}, {"poc_verified": False}, {"poc_verified": "true"}, {"poc_verified": True}]
)
def test_preflight_or_boolean_alone_never_qualifies(profile):
    status = qualification_status(profile, "a" * 64, "b" * 64)
    assert not status.qualified
    assert status.reason
    with pytest.raises(AppError, match="executed"):
        require_qualified(profile, "a" * 64, "b" * 64)


def test_executed_evidence_is_bound_to_current_graph_and_slots():
    profile = evidence_profile()
    assert qualification_status(profile, "a" * 64, "b" * 64).qualified
    assert not qualification_status(profile, "0" * 64, "b" * 64).qualified
    assert not qualification_status(profile, "a" * 64, "0" * 64).qualified


@pytest.mark.parametrize(
    "field",
    [
        "tested_at",
        "prompt_id",
        "model_hash",
        "comfyui_commit",
        "custom_node_versions",
        "lora_hashes",
        "output",
    ],
)
def test_missing_runtime_or_media_evidence_blocks_enable(field):
    profile = evidence_profile()
    del profile["execution_evidence"][field]
    assert not qualification_status(profile, "a" * 64, "b" * 64).qualified


@pytest.mark.parametrize(
    "field,value",
    [
        ("width", 0),
        ("height", -1),
        ("fps", 0),
        ("fps", True),
        ("duration_seconds", True),
        ("duration_seconds", float("nan")),
        ("has_video", False),
        ("has_audio", False),
        ("checksum", "unmeasured"),
        ("size_bytes", 0),
    ],
)
def test_invalid_actual_media_evidence_never_qualifies(field, value):
    profile = copy.deepcopy(evidence_profile())
    profile["execution_evidence"]["output"][field] = value
    assert not qualification_status(profile, "a" * 64, "b" * 64).qualified


def test_declared_lora_must_match_executed_lora_hashes():
    profile = evidence_profile()
    profile["lora_hashes"] = {"realism": "1" * 64}
    assert not qualification_status(profile, "a" * 64, "b" * 64).qualified
    profile["execution_evidence"]["lora_hashes"] = {"realism": "1" * 64}
    assert qualification_status(profile, "a" * 64, "b" * 64).qualified


@pytest.mark.parametrize("declaration", [None, [], "bad", {"node": 123}])
def test_malformed_declared_nodes_return_unqualified(declaration):
    profile = evidence_profile()
    profile["custom_node_versions"] = declaration
    assert not qualification_status(profile, "a" * 64, "b" * 64).qualified
