"""Synthetic assertions at qualification seams; never release evidence."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.qualification import (
    director_settings_identity,
    qualified_audio_modes,
    qualified_features,
    require_settings,
    verified_settings,
)
from apps.api.app.services.director_run_service import (
    director_aggregate_settings,
    require_director_aggregate_qualification,
)
from apps.api.app.services.generation_intent import stable_hash
from apps.api.app.services.workflow_contracts import profile_hash, require_contract
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from tests.unit.test_director_execution_contract import candidate
from tests.unit.test_refine_face_qualification import settings
from tests.unit.test_workflow_qualification import evidence_profile


def qualified_record():
    spec, graph = candidate()
    profile = deepcopy(spec.provenance["workflow_profile"])
    approved = ApprovedWorkflow("t2v", "synthetic", graph, {})
    # Keep the registry's real patch bindings.
    import json
    from pathlib import Path

    entry = next(
        x
        for x in json.loads(Path("workflows/h3/registry.json").read_text())["workflows"]
        if x["code"] == "H3_DIRECTOR_T2V_BASE"
    )
    approved.slots.update({key: tuple(value) for key, value in entry["slots"].items()})
    profile["weight_hashes"] = {
        value: "a" * 64
        for node in graph.values()
        for key, value in node["inputs"].items()
        if key in {"unet_name", "clip_name", "vae_name", "latent_upscale_model", "detector"}
    }
    profile["dependency_versions"] = {node["class_type"]: "synthetic" for node in graph.values()}
    evidence = deepcopy(evidence_profile()["execution_evidence"])
    evidence.update(
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
        weight_hashes=deepcopy(profile["weight_hashes"]),
        dependency_versions=deepcopy(profile["dependency_versions"]),
        profile_hash=profile_hash(profile),
    )
    evidence["combinations"] = [
        {
            "mode": "t2v",
            "quality_profile": "BASE",
            "aspect_ratio": "16:9",
            "width": 864,
            "height": 480,
            "steps": 25,
            "output": deepcopy(evidence["output"]),
        }
    ]
    profile.update(poc_verified=True, execution_evidence=evidence)
    record = SimpleNamespace(
        profile=profile, mode="t2v", quality_profile="BASE", execution_scope="single_scene"
    )
    return record, approved, spec


@pytest.mark.parametrize(
    "filename", ["minimax_h3_latent_upscaler_3d_bf16.safetensors", "face_yolov8m.pt"]
)
def test_auxiliary_weight_digest_must_cover_graph_and_match_evidence(filename):
    record, approved, _ = qualified_record()
    require_contract(record, approved)
    record.profile["weight_hashes"][filename] = "b" * 64
    record.profile["execution_evidence"]["profile_hash"] = profile_hash(record.profile)
    with pytest.raises(AppError):
        require_contract(record, approved)


def test_base_template_cannot_omit_auxiliary_weight_coverage():
    record, approved, _ = qualified_record()
    require_contract(record, approved)
    for filename in ("minimax_h3_latent_upscaler_3d_bf16.safetensors", "face_yolov8m.pt"):
        del record.profile["weight_hashes"][filename]
    record.profile["execution_evidence"].update(
        weight_hashes=deepcopy(record.profile["weight_hashes"]),
        profile_hash=profile_hash(record.profile),
    )
    with pytest.raises(AppError):
        require_contract(record, approved)


def settings_case(profile, config, *, output_canvas=(864, 480)):
    case = deepcopy(profile["execution_evidence"])
    canvas = {"width": 864, "height": 480}
    case.update(
        settings=deepcopy(config),
        base_canvas=canvas,
        task="t2v",
        settings_hash=director_settings_identity(config, base_canvas=canvas, task="t2v"),
    )
    case["output"].update(width=output_canvas[0], height=output_canvas[1])
    profile["execution_evidence"]["director_settings"] = [case]
    return case


@pytest.mark.parametrize("key", ["profile_hash", "weight_hashes", "dependency_versions"])
def test_single_settings_bind_full_provenance(key):
    record, _, _ = qualified_record()
    config = settings(True, False)
    case = settings_case(record.profile, config)
    require_settings(record.profile, config)
    case[key] = "b" * 64 if key == "profile_hash" else {"changed": "b" * 64}
    with pytest.raises(AppError):
        require_settings(record.profile, config)


@pytest.mark.parametrize(
    "mode,expected",
    [("refine", (864, 480)), ("upscale", (1952, 1088)), ("latent_upscale", (1952, 1088))],
)
@pytest.mark.parametrize("face", [False, True])
def test_single_settings_measure_canonical_output_canvas(mode, expected, face):
    record, _, _ = qualified_record()
    config = settings(True, face)
    config["refine"].update(mode=mode, megapixels=2.0)
    case = settings_case(record.profile, config, output_canvas=expected)
    require_settings(record.profile, config)
    case["output"]["width"] += 32
    with pytest.raises(AppError):
        require_settings(record.profile, config)


@pytest.mark.parametrize(
    "mode,expected",
    [("refine", (864, 480)), ("upscale", (1952, 1088)), ("latent_upscale", (1952, 1088))],
)
@pytest.mark.parametrize("face", [False, True])
def test_aggregate_uses_the_same_canvas(mode, expected, face):
    record, _, base = qualified_record()
    profile = record.profile
    profile.update(
        export_mode="segments",
        output_artifacts=[
            {
                "role": "segment",
                "node_id": "director",
                "coverage": "all_members",
                "required": True,
                "transport": "studio_native_segments_v1",
            }
        ],
    )
    profile["execution_evidence"]["profile_hash"] = profile_hash(profile)
    spec = DirectorExecutionSpec.finalize(
        **{
            **base.model_dump(),
            "profile_hash": profile_hash(profile),
            "member_count": 2,
            "frames": 248,
            "timeline": [
                {"scene_id": "first", "frame_count": 124},
                {"scene_id": "second", "frame_count": 124},
            ],
            "requested_duration_seconds": 10,
            "resolved_duration_seconds": 248 / 24,
            "refine": {**settings(True)["refine"], "mode": mode, "megapixels": 2.0},
            "face_refine": settings(False, face)["face_refine"],
        }
    )
    exact = director_aggregate_settings(
        spec,
        member_count=2,
        continuities=["CUT", "CONTINUOUS"],
        output_artifacts=profile["output_artifacts"],
    )
    case = deepcopy(profile["execution_evidence"])
    case.update(
        member_count=2,
        task="t2v",
        continuities=["CUT", "CONTINUOUS"],
        settings=exact,
        settings_hash=stable_hash(exact),
    )
    case["output"].update(width=expected[0], height=expected[1])
    profile["execution_evidence"]["director_aggregate_cases"] = [case]
    require_director_aggregate_qualification(
        profile, spec, member_count=2, continuities=["CUT", "CONTINUOUS"]
    )
    case["output"]["width"] += 32
    with pytest.raises(AppError):
        require_director_aggregate_qualification(
            profile, spec, member_count=2, continuities=["CUT", "CONTINUOUS"]
        )


def test_director_base_combination_also_checks_actual_canvas():
    record, approved, _ = qualified_record()
    require_contract(record, approved)
    record.profile["execution_evidence"]["combinations"][0]["output"]["width"] = 640
    with pytest.raises(AppError):
        require_contract(record, approved)


def test_director_baseline_output_must_match_an_executed_canvas():
    record, approved, _ = qualified_record()
    require_contract(record, approved)
    record.profile["execution_evidence"]["output"]["width"] = 640
    with pytest.raises(AppError):
        require_contract(record, approved)


@pytest.mark.parametrize(
    "filename", ["minimax_h3_latent_upscaler_3d_bf16.safetensors", "face_yolov8m.pt"]
)
def test_old_settings_evidence_cannot_survive_same_filename_new_bytes(filename):
    record, _, _ = qualified_record()
    config = settings(True, True)
    settings_case(record.profile, config)
    require_settings(record.profile, config)
    # Current measured baseline is refreshed, but the optional-settings case is old.
    record.profile["weight_hashes"][filename] = "b" * 64
    record.profile["execution_evidence"]["weight_hashes"] = deepcopy(
        record.profile["weight_hashes"]
    )
    record.profile["execution_evidence"]["profile_hash"] = profile_hash(record.profile)
    with pytest.raises(AppError):
        require_settings(record.profile, config)


@pytest.mark.parametrize("key", ["profile_hash", "weight_hashes", "dependency_versions"])
def test_single_settings_reject_missing_full_provenance(key):
    record, _, _ = qualified_record()
    config = settings(True)
    case = settings_case(record.profile, config)
    require_settings(record.profile, config)
    del case[key]
    with pytest.raises(AppError):
        require_settings(record.profile, config)


def test_single_settings_are_bound_to_requested_canvas_and_task():
    record, _, _ = qualified_record()
    config = settings(True)
    settings_case(record.profile, config)
    require_settings(record.profile, config, base_canvas=(864, 480), task="t2v")
    for canvas, task in [((480, 864), "t2v"), ((864, 480), "r2v")]:
        with pytest.raises(AppError):
            require_settings(record.profile, config, base_canvas=canvas, task=task)


def test_settings_cannot_select_a_detector_without_its_own_bound_digest():
    record, _, _ = qualified_record()
    config = settings(False, True)
    config["face_refine"]["detector"] = "face_yolov8n.pt"
    settings_case(record.profile, config)
    with pytest.raises(AppError):
        require_settings(record.profile, config)


def test_equal_upscaled_outputs_cannot_qualify_different_input_canvases():
    record, _, _ = qualified_record()
    baseline = record.profile["execution_evidence"]
    second = deepcopy(baseline["combinations"][0])
    second.update(width=1728, height=960)
    second["output"].update(width=1728, height=960)
    baseline["combinations"].append(second)
    config = settings(True)
    config["refine"].update(mode="upscale", megapixels=2.0)
    settings_case(record.profile, config, output_canvas=(1952, 1088))
    assert len(verified_settings(record.profile)) == 1
    require_settings(record.profile, config, base_canvas=(864, 480), task="t2v")
    with pytest.raises(AppError):
        require_settings(record.profile, config, base_canvas=(1728, 960), task="t2v")


@pytest.mark.parametrize("field", ["base_canvas", "task"])
def test_settings_missing_executed_input_context_fail_closed(field):
    record, _, _ = qualified_record()
    config = settings(True)
    case = settings_case(record.profile, config)
    del case[field]
    case["settings_hash"] = director_settings_identity(
        config, base_canvas=case.get("base_canvas"), task=case.get("task")
    )
    assert verified_settings(record.profile) == []
    with pytest.raises(AppError):
        require_settings(record.profile, config)


def test_plan_builder_rejects_canvas_collision_before_freezing():
    from apps.api.app.providers.minimax_h3_director.plan_builder import DirectorPlanBuilder
    from apps.api.app.services.generation_intent import GenerationIntent

    record, _, spec = qualified_record()
    config = settings(True)
    config["refine"].update(mode="upscale", megapixels=2.0)
    settings_case(record.profile, config, output_canvas=(1952, 1088))
    values = {
        key: value
        for key, value in spec.model_dump().items()
        if key in GenerationIntent.model_fields
    }
    values.update(config, provider_task=spec.task)
    values["canvas"] = {"width": 1728, "height": 960, "aspect_ratio": "16:9"}
    intent = GenerationIntent.model_validate(values)
    with pytest.raises(AppError):
        DirectorPlanBuilder().build(
            intent,
            output_prefix="synthetic",
            workflow=record,
            profile_hash=profile_hash(record.profile),
        )


def test_old_settings_only_hash_does_not_qualify_context_bound_case():
    record, _, _ = qualified_record()
    config = settings(True)
    case = settings_case(record.profile, config)
    case["settings_hash"] = stable_hash(config)
    assert verified_settings(record.profile) == []
    with pytest.raises(AppError):
        require_settings(record.profile, config, base_canvas=(864, 480), task="t2v")


@pytest.mark.parametrize("canvas,task", [((1728, 960), "t2v"), ((864, 480), "r2v")])
def test_capability_helpers_filter_task_and_canvas(canvas, task):
    record, _, _ = qualified_record()
    config = settings(True, True)
    config["audio_policy"]["mode"] = "mute"
    settings_case(record.profile, config)
    assert qualified_audio_modes(record.profile, base_canvas=(864, 480), task="t2v") == [
        "generate",
        "mute",
    ]
    assert verified_settings(record.profile, base_canvas=canvas, task=task) == []
    assert not any(qualified_features(record.profile, base_canvas=canvas, task=task).values())
    assert qualified_audio_modes(record.profile, base_canvas=canvas, task=task) == ["generate"]
