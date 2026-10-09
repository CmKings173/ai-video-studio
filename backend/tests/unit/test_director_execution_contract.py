import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.plan_builder import DirectorPlanBuilder
from apps.api.app.providers.minimax_h3_director.timeline import build_timeline
from apps.api.app.providers.minimax_h3_director.workflow_builder import DirectorWorkflowBuilder
from apps.api.app.services.generation_intent import GenerationIntent
from apps.api.app.services.workflow_contracts import profile_hash
from apps.api.app.services.workflow_registry import ApprovedWorkflow


def candidate(task="t2v", **overrides):
    directory = Path(__file__).resolve().parents[2] / "workflows" / "h3"
    entries = json.loads((directory / "registry.json").read_text(encoding="utf-8"))["workflows"]
    entry = next(item for item in entries if item["code"] == f"H3_DIRECTOR_{task.upper()}_BASE")
    graph = json.loads((directory / entry["file"]).read_text(encoding="utf-8"))
    approved = ApprovedWorkflow(
        task, entry["version"], graph, {key: tuple(value) for key, value in entry["slots"].items()}
    )
    workflow = SimpleNamespace(
        id="candidate",
        code=entry["code"],
        version=entry["version"],
        workflow_hash=approved.workflow_hash,
        slot_map_hash=approved.slot_map_hash,
        profile=entry["profile"],
    )
    values = {
        "requested_mode": task,
        "provider_task": task,
        "prompt": "Exact accepted prompt",
        "canvas": {"width": 864, "height": 480, "aspect_ratio": "16:9"},
        "quality_profile": "BASE",
        "seed": 42,
        "seed_policy": "FIXED",
        "steps": 25,
        "frames": 124,
        "requested_duration_seconds": 5,
        "resolved_duration_seconds": 124 / 24,
        **overrides,
    }
    intent = GenerationIntent.model_validate(values)
    spec = DirectorPlanBuilder().build(
        intent,
        output_prefix="studio/test",
        workflow=workflow,
        profile_hash=profile_hash(entry["profile"]),
    )
    return spec, graph


def test_execution_hash_roundtrip_and_tampering_detection():
    spec, _ = candidate()
    frozen = spec.model_dump(mode="json")
    assert DirectorExecutionSpec.model_validate(frozen).execution_hash == spec.execution_hash
    frozen["canvas"]["width"] = 896
    with pytest.raises(ValidationError, match="hash"):
        DirectorExecutionSpec.model_validate(frozen)


def test_safe_seed_and_custom_canvas_boundaries():
    spec, _ = candidate(
        seed=9_007_199_254_740_991, canvas={"width": 640, "height": 960, "aspect_ratio": "Custom"}
    )
    assert spec.seed == 9_007_199_254_740_991
    with pytest.raises(ValidationError):
        candidate(seed=9_007_199_254_740_992)
    with pytest.raises(ValidationError):
        candidate(canvas={"width": 641, "height": 960, "aspect_ratio": "Custom"})


def test_builder_preserves_template_and_exact_prompt():
    spec, template = candidate()
    original = json.dumps(template, sort_keys=True)
    graph = DirectorWorkflowBuilder().build(base_workflow=template, spec=spec, staged_assets={})
    director = next(node for node in graph.values() if node["class_type"] == "MiniMaxH3Director")
    assert director["inputs"]["global_prompt"] == "Exact accepted prompt"
    assert json.dumps(template, sort_keys=True) == original


def test_reference_ordinals_are_stable_and_source_video_is_distinct():
    assets = [
        {
            "id": str(index),
            "role": "REFERENCE_IMAGE",
            "order_index": index,
            "checksum": "a" * 64,
            "filename": f"image-{index}.png",
            "content_type": "image/png",
        }
        for index in range(3)
    ]
    spec, _ = candidate("r2v", assets=assets)
    timeline = build_timeline(
        spec, {("REFERENCE_IMAGE", index): f"image-{index}.png" for index in range(3)}
    )
    assert [item["imageFile"] for item in timeline["segments"][0]["refs"]] == [
        f"image-{index}.png" for index in range(3)
    ]
    source = {
        "id": "source",
        "role": "SOURCE_VIDEO",
        "order_index": 0,
        "checksum": "b" * 64,
        "filename": "source.mp4",
        "content_type": "video/mp4",
    }
    spec, _ = candidate("v2v", assets=[source])
    timeline = build_timeline(spec, {("SOURCE_VIDEO", 0): "input/source.mp4"})
    assert timeline["video"]["videoFile"] == "input/source.mp4"
    assert timeline["segments"][0]["refVideos"] == []


def test_fl2v_uses_source_shot_contract_for_ending_frame():
    asset = {
        "id": "last",
        "role": "LAST_FRAME",
        "order_index": 0,
        "checksum": "c" * 64,
        "filename": "last.png",
        "content_type": "image/png",
    }
    spec, _ = candidate("fl2v", requested_mode="i2v_last", assets=[asset])
    timeline = build_timeline(spec, {("LAST_FRAME", 0): "last.png"})
    assert timeline["timelineMode"] == "fl2v"
    assert timeline["shots"][0]["endImage"]["imageFile"] == "last.png"
