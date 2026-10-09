import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.api.app.core.errors import AppError
from apps.api.app.schemas.api import GenerationRequest, SceneGenerationConfig
from apps.api.app.services.h3_validator import H3Profile, H3Request, H3ValidationError, H3Validator
from apps.api.app.services.workflow_contracts import (
    require_asset_slot,
    require_contract,
    resolve_canvas,
)
from apps.api.app.services.workflow_registry import ApprovedWorkflow, _stable_hash
from apps.api.app.services.workflow_router import RoutingInput, select_mode


def contract_fixture():
    graph = {
        "1": {
            "class_type": "TestFixture",
            "inputs": {
                "prompt": "",
                "seed": 1,
                "width": 864,
                "height": 480,
                "length": 124,
                "steps": 20,
                "prefix": "",
                "sampler": "synthetic",
                "scheduler": "synthetic",
            },
        }
    }
    slots = {
        key: ("1", value)
        for key, value in {
            "PROMPT": "prompt",
            "SEED": "seed",
            "WIDTH": "width",
            "HEIGHT": "height",
            "FRAMES": "length",
            "STEPS": "steps",
            "OUTPUT_PREFIX": "prefix",
        }.items()
    }
    approved = ApprovedWorkflow("t2v", "test", graph, slots)
    profile = {
        "quality_profile": "STANDARD",
        "steps": 20,
        "fps": 24,
        "sampler": "synthetic",
        "scheduler": "synthetic",
        "turbo": False,
        "resolution": {"kind": "explicit_slots", "canvases": {"16:9": [864, 480]}},
        "duration_resolver": {"kind": "frames_slots"},
        "dependency_versions": {"TestFixture": "test-only"},
        "setting_bindings": {"sampler": ["1", "sampler"], "scheduler": ["1", "scheduler"]},
    }
    profile_hash = _stable_hash(profile)
    profile.update(
        poc_verified=True,
        execution_evidence={
            "executed": True,
            "workflow_hash": approved.workflow_hash,
            "slot_map_hash": approved.slot_map_hash,
            "profile_hash": profile_hash,
            "tested_at": "2026-10-05T00:00:00Z",
            "prompt_id": "synthetic-only",
            "model_hash": "c" * 64,
            "lora_hashes": {},
            "comfyui_commit": "d" * 40,
            "custom_node_versions": {},
            "dependency_versions": profile["dependency_versions"],
            "combinations": [
                {
                    "mode": "t2v",
                    "quality_profile": "STANDARD",
                    "aspect_ratio": "16:9",
                    "width": 864,
                    "height": 480,
                    "steps": 20,
                    "output": {
                        "width": 864,
                        "height": 480,
                        "fps": 24,
                        "duration_seconds": 124 / 24,
                        "has_video": True,
                        "has_audio": True,
                        "checksum": "f" * 64,
                        "size_bytes": 1000,
                    },
                }
            ],
            "output": {
                "width": 864,
                "height": 480,
                "fps": 24,
                "duration_seconds": 124 / 24,
                "has_video": True,
                "has_audio": True,
                "checksum": "f" * 64,
                "size_bytes": 1000,
            },
        },
    )
    return SimpleNamespace(
        profile=profile,
        execution_scope="single_scene",
        quality_profile="STANDARD",
        mode="t2v",
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
    ), approved


def test_contract_binds_settings_dependencies_and_ratios():
    record, graph = contract_fixture()
    assert require_contract(record, graph, "STANDARD", "16:9").steps == 20
    for mutation in ["steps", "dependency", "ratio"]:
        changed = copy.deepcopy(record)
        if mutation == "steps":
            changed.profile["steps"] = 8
        elif mutation == "dependency":
            changed.profile["execution_evidence"]["dependency_versions"] = {}
        with pytest.raises(AppError):
            require_contract(changed, graph, "STANDARD", "1:1" if mutation == "ratio" else "16:9")


def test_selector_uses_pinned_binary_megapixels_and_post_rounding_cap():
    graph = ApprovedWorkflow(
        "t2v",
        "test",
        {
            "115": {
                "class_type": "ResolutionSelector",
                "inputs": {"aspect_ratio": "1:1 (Square)", "megapixels": 0.4, "multiple": 32},
            }
        },
        {"ASPECT_RATIO": ("115", "aspect_ratio")},
    )
    resolution = {
        "kind": "resolution_selector",
        "node_id": "115",
        "source_sha256": "3fe5c3f9aeceed343725a91c6feaa13377b5ebad2c95fd633e3806d69d10bcc8",
        "aspect_ratio_values": {"1:1": "1:1 (Square)", "16:9": "16:9 (Widescreen)"},
    }
    assert resolve_canvas(resolution, graph, "1:1") == (640, 640)
    assert resolve_canvas(resolution, graph, "16:9") == (864, 480)
    graph.workflow["115"]["inputs"]["megapixels"] = 1.0
    with pytest.raises(AppError):
        resolve_canvas(resolution, graph, "16:9")


def test_last_frame_and_fractional_duration_match_graph():
    assert select_mode(RoutingInput(last_frame_asset_id="last")) == "i2v_last"
    value = H3Validator(H3Profile()).validate(
        H3Request(
            mode="i2v_last",
            width=864,
            height=480,
            duration_seconds=124.1 / 24,
            last_frame_asset_id="last",
        )
    )
    assert value.frames == 124


@pytest.mark.parametrize(
    "values",
    [
        {"reference_audio_asset_ids": ["a"], "reference_audio_durations": [2]},
        {"reference_video_asset_ids": ["v"], "reference_video_durations": [1]},
        {"reference_video_asset_ids": ["v", "w"], "reference_video_durations": [8, 8]},
        {
            "reference_image_asset_ids": [str(i) for i in range(9)],
            "reference_video_asset_ids": ["v", "w", "x"],
            "reference_video_durations": [2, 2, 2],
            "reference_audio_asset_ids": ["a"],
            "reference_audio_durations": [2],
        },
    ],
)
def test_ref2va_rejects_invalid_published_envelope(values):
    with pytest.raises(H3ValidationError):
        H3Validator(H3Profile()).validate(
            H3Request(mode="r2v", width=864, height=480, duration_seconds=5, **values)
        )


def test_exact_prompt_and_explicit_override_fields_survive_schema():
    prompt = "  accepted\n<Picture 1>  "
    request = GenerationRequest(
        execution_prompt=prompt, source_scene_revision=1, source_video_revision=1
    )
    assert request.execution_prompt == prompt
    assert request.model_dump(exclude_unset=True)["execution_prompt"] == prompt
    assert "steps" not in request.model_fields_set
    assert SceneGenerationConfig().model_dump(exclude_unset=True) == {}
    with pytest.raises(ValidationError):
        SceneGenerationConfig(seed_policy="FIXED")


def test_native_reference_slots_must_reach_real_dotted_inputs():
    graph = {
        "h3": {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {"ref_images.ref_image_0": ["image", 0]},
        },
        "image": {"class_type": "LoadImage", "inputs": {"image": "old.png"}},
        "unused": {"class_type": "LoadImage", "inputs": {"image": "ignored.png"}},
    }
    approved = ApprovedWorkflow(
        "r2v",
        "test-only",
        graph,
        {"REFERENCE_IMAGE_1": ("image", "image"), "REFERENCE_IMAGE_2": ("unused", "image")},
    )
    require_asset_slot(approved, "REFERENCE_IMAGE_1")
    with pytest.raises(AppError, match="conditioning"):
        require_asset_slot(approved, "REFERENCE_IMAGE_2")


def test_each_combination_needs_matching_actual_output():
    record, graph = contract_fixture()
    record.profile["execution_evidence"]["combinations"][0]["output"]["width"] = 640
    with pytest.raises(AppError, match="matching measured"):
        require_contract(record, graph)


@pytest.mark.parametrize("container", [None, [], "not-a-map"])
def test_malformed_canvas_map_returns_contract_error(container):
    record, graph = contract_fixture()
    resolution = {"kind": "explicit_slots", "canvases": container}
    with pytest.raises(AppError):
        resolve_canvas(resolution, graph, "16:9")


def native_contract_fixture():
    from apps.api.app.services.workflow_contracts import SELECTOR_SHA256, profile_hash
    from tests.historical_workflow_fixtures import historical_manifests

    entry = historical_manifests()[0]
    graph = entry["workflow"]
    approved = ApprovedWorkflow(
        "t2v", "test-only", graph, {key: tuple(value) for key, value in entry["slots"].items()}
    )
    record, _ = contract_fixture()
    profile = record.profile
    profile.update(
        sampler="res_multistep",
        scheduler="simple",
        resolution={
            "kind": "resolution_selector",
            "node_id": "115",
            "source_sha256": SELECTOR_SHA256,
            "aspect_ratio_values": {"16:9": "16:9 (Widescreen)"},
        },
        duration_resolver={"kind": "template_expression", "node_id": "107"},
        dependency_versions={node["class_type"]: "test-only" for node in graph.values()},
        setting_bindings={"sampler": ["17", "sampler_name"], "scheduler": ["9", "scheduler"]},
        weight_hashes={
            node["inputs"][key]: "a" * 64
            for node in graph.values()
            for key in ("unet_name", "clip_name", "vae_name")
            if key in node["inputs"]
        },
        output_node="92",
    )
    profile["execution_evidence"].update(
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
        profile_hash=profile_hash(profile),
        dependency_versions=profile["dependency_versions"],
        weight_hashes=profile["weight_hashes"],
    )
    return record, approved


@pytest.mark.parametrize("binding", ["STEPS", "SEED", "OUTPUT_PREFIX", "PROMPT"])
def test_native_execution_rejects_disconnected_patch_slots(binding):
    from apps.api.app.services.workflow_contracts import profile_hash

    record, graph = native_contract_fixture()
    assert require_contract(record, graph).steps == 20
    node, field = graph.slots[binding]
    graph.workflow["unused"] = copy.deepcopy(graph.workflow[node])
    graph.slots[binding] = ("unused", field)
    record.profile["dependency_versions"] = {
        n["class_type"]: "test-only" for n in graph.workflow.values()
    }
    record.profile["execution_evidence"].update(
        workflow_hash=graph.workflow_hash,
        slot_map_hash=graph.slot_map_hash,
        profile_hash=profile_hash(record.profile),
        dependency_versions=record.profile["dependency_versions"],
    )
    with pytest.raises(AppError):
        require_contract(record, graph)


@pytest.mark.parametrize("values", [{"fps": True}, {"frame_step": 0}, {"max_reference_audio": "3"}])
def test_invalid_h3_profile_is_rejected_at_boundary(values):
    with pytest.raises(ValueError):
        H3Profile(**values)


def test_historical_frozen_graph_is_read_only_regardless_of_frame_count():
    from apps.api.app.services.workflow_contracts import require_frozen

    record, graph = native_contract_fixture()
    record.workflow, record.slots = graph.workflow, graph.slots
    record.version, record.required_slots = "test-only", []
    record.workflow_hash, record.slot_map_hash = graph.workflow_hash, graph.slot_map_hash
    frozen = {
        "schema_version": 1,
        "workflow": graph.patch({"DURATION": 5}),
        "duration_seconds": 5,
        "frames": 124,
        "width": 864,
        "height": 480,
    }
    with pytest.raises(AppError) as caught:
        require_frozen(frozen, record)
    assert caught.value.code == "WORKFLOW_RETIRED"
    frozen["frames"] = 141
    with pytest.raises(AppError):
        require_frozen(frozen, record)


@pytest.mark.parametrize("container", [None, [], "not-a-map"])
def test_malformed_registry_container_is_rejected_as_unqualified(container):
    from apps.api.app.services.workflow_contracts import approved_record

    record, graph = native_contract_fixture()
    record.workflow, record.slots = graph.workflow, container
    record.version, record.required_slots = "test-only", []
    record.workflow_hash, record.slot_map_hash = graph.workflow_hash, graph.slot_map_hash
    with pytest.raises(AppError):
        approved_record(record)


def test_advertised_canvas_must_fit_qualified_profile_limits():
    from apps.api.app.services.workflow_contracts import profile_hash

    record, graph = contract_fixture()
    record.profile["max_pixels"] = 100000
    record.profile["execution_evidence"]["profile_hash"] = profile_hash(record.profile)
    with pytest.raises(AppError):
        require_contract(record, graph)


def test_capability_ratio_must_be_supported_by_request_schema():
    from apps.api.app.services.workflow_contracts import profile_hash

    record, graph = contract_fixture()
    record.profile["resolution"]["canvases"] = {"5:7": [512, 768]}
    case = record.profile["execution_evidence"]["combinations"][0]
    case.update(aspect_ratio="5:7", width=512, height=768)
    case["output"].update(width=512, height=768)
    record.profile["execution_evidence"]["profile_hash"] = profile_hash(record.profile)
    with pytest.raises(AppError):
        require_contract(record, graph)
