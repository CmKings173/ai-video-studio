from copy import deepcopy

import pytest

from apps.api.app.providers.minimax_h3_director.workflow_builder import (
    DirectorWorkflowBuilder,
    DirectorWorkflowError,
)
from tests.unit.test_refine_face_graph import candidate


@pytest.mark.parametrize(
    "damage",
    [
        "missing_latent",
        "missing_sampler",
        "missing_seed",
        "missing_face_group",
        "sigma_index",
        "sigma_bool_index",
        "sigma_class",
        "missing_sigmas",
        "sigma_model",
        "missing_scheduler_model",
        "scheduler_denoise",
        "unknown_socket",
        "duplicate_scheduler",
        "empty_config",
        "unknown_field",
        "primitive_link",
        "invalid_face_select",
        "model_index",
    ],
)
def test_builder_rejects_incomplete_or_ambiguous_source_topology(damage):
    spec, graph = candidate(refine={"enabled": True}, face_refine={"enabled": True})
    nodes = {node["class_type"]: (key, node) for key, node in graph.items()}
    refine = nodes["MiniMaxH3DirectorRefine"][1]["inputs"]
    face = nodes["MiniMaxH3DirectorFaceRefine"][1]["inputs"]
    scheduler_id, scheduler = nodes["BasicScheduler"]
    if damage.startswith("missing_") and damage in {
        "missing_latent",
        "missing_sampler",
        "missing_seed",
    }:
        del refine[
            {
                "missing_latent": "latent_upscale_model",
                "missing_sampler": "sampler",
                "missing_seed": "seed",
            }[damage]
        ]
    elif damage == "missing_face_group":
        del face["bd_grp_face_detect"]
    elif damage == "sigma_index":
        refine["sigmas"] = [scheduler_id, 1]
    elif damage == "sigma_bool_index":
        refine["sigmas"] = [scheduler_id, False]
    elif damage == "sigma_class":
        scheduler["class_type"] = "UNETLoader"
    elif damage == "missing_sigmas":
        del refine["sigmas"]
    elif damage == "sigma_model":
        scheduler["inputs"]["model"] = [nodes["CLIPLoader"][0], 0]
    elif damage == "missing_scheduler_model":
        del scheduler["inputs"]["model"]
    elif damage == "scheduler_denoise":
        scheduler["inputs"]["denoise"] = 0.8
    elif damage == "unknown_socket":
        refine["enabled"] = True
    elif damage == "duplicate_scheduler":
        graph["duplicate"] = deepcopy(scheduler)
    elif damage == "empty_config":
        refine.clear()
    elif damage == "unknown_field":
        face["guess"] = "no"
    elif damage == "primitive_link":
        face["steps"] = [scheduler_id, 0]
    elif damage == "invalid_face_select":
        face["select"] = "all_faces"
    elif damage == "model_index":
        model = [nodes["UNETLoader"][0], 1]
        refine["refine_model"] = model
        scheduler["inputs"]["model"] = model
        nodes["MiniMaxH3Director"][1]["inputs"]["model"] = model
    with pytest.raises(DirectorWorkflowError):
        DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})


@pytest.mark.parametrize("geometry", [{"width": 1280}, {"height": 720}, {"aspect_ratio": "16:9"}])
def test_enabled_refine_rejects_source_ignored_custom_geometry(geometry):
    spec, graph = candidate(refine={"enabled": True, **geometry})
    with pytest.raises(DirectorWorkflowError):
        DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})


def test_complete_mapping_keeps_zero_sentinels_and_independent_seed():
    spec, graph = candidate(
        refine={
            "enabled": True,
            "seed_mode": "independent",
            "passes": 3,
            "enable_tiling": True,
            "enable_latent_chunking": True,
        },
        face_refine={"enabled": True, "select": "centre_most", "seed_mode": "offset"},
    )
    built = DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})
    inputs = {node["class_type"]: node["inputs"] for node in built.values()}
    refine = inputs["MiniMaxH3DirectorRefine"]
    assert refine["seed"] == 42
    assert refine["seed_mode"] == "independent"
    assert (refine["width"], refine["height"], refine["megapixels"]) == (0, 0, 0.0)
    assert refine["aspect_ratio"] == "\u8ddf\u968f\u5bfc\u6f14\u53f0"
    assert refine["enable_tiling"] is True and refine["enable_latent_chunking"] is True
    assert inputs["MiniMaxH3DirectorFaceRefine"]["select"] == "centre_most"
    assert "enabled" not in refine


def test_historical_unwired_base_graph_remains_compatible():
    spec, graph = candidate()
    for key, node in list(graph.items()):
        if node["class_type"] in {
            "BasicScheduler",
            "MiniMaxH3DirectorRefine",
            "MiniMaxH3DirectorFaceRefine",
        }:
            del graph[key]
    director = next(node for node in graph.values() if node["class_type"] == "MiniMaxH3Director")
    director["inputs"].pop("refine")
    director["inputs"].pop("face_refine")
    DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})


def installed_info(graph):
    info = {}
    for node in graph.values():
        fields = {}
        for key in node["inputs"]:
            kind = (
                "MODEL"
                if key in {"model", "refine_model"}
                else "SIGMAS"
                if key == "sigmas"
                else "STRING"
            )
            fields[key] = [kind, {}]
        info[node["class_type"]] = {"input": {"required": fields}}
    director = info["MiniMaxH3Director"]["input"]["required"]
    director["refine"] = ["MMX_DIR_REFINE", {}]
    director["face_refine"] = ["MMX_DIR_FACE_REFINE", {}]
    info["MiniMaxH3DirectorRefine"]["output"] = ["MMX_DIR_REFINE", "INT", "INT"]
    info["MiniMaxH3DirectorFaceRefine"]["output"] = ["MMX_DIR_FACE_REFINE"]
    info["BasicScheduler"]["output"] = ["SIGMAS"]
    info["UNETLoader"]["output"] = ["MODEL"]
    return info


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "refine_output",
        "face_output",
        "scheduler_output",
        "sigma_input",
        "model_input",
        "director_input",
        "model_output",
        "refine_model_input",
    ],
)
def test_installed_socket_types_are_checked_when_object_info_exists(damage):
    spec, graph = candidate(refine={"enabled": True}, face_refine={"enabled": True})
    info = installed_info(graph)
    if damage == "refine_output":
        info["MiniMaxH3DirectorRefine"]["output"][0] = "MODEL"
    elif damage == "face_output":
        info["MiniMaxH3DirectorFaceRefine"]["output"] = []
    elif damage == "scheduler_output":
        info["BasicScheduler"]["output"][0] = "MODEL"
    elif damage == "sigma_input":
        info["MiniMaxH3DirectorRefine"]["input"]["required"]["sigmas"][0] = "MODEL"
    elif damage == "model_input":
        info["BasicScheduler"]["input"]["required"]["model"][0] = "SIGMAS"
    elif damage == "director_input":
        info["MiniMaxH3Director"]["input"]["required"]["face_refine"][0] = "MODEL"
    elif damage == "model_output":
        info["UNETLoader"]["output"][0] = "CLIP"
    elif damage == "refine_model_input":
        info["MiniMaxH3DirectorRefine"]["input"]["required"]["refine_model"][0] = "SIGMAS"
    if damage:
        with pytest.raises(DirectorWorkflowError):
            DirectorWorkflowBuilder().build(
                base_workflow=graph, spec=spec, staged_assets={}, object_info=info
            )
    else:
        DirectorWorkflowBuilder().build(
            base_workflow=graph, spec=spec, staged_assets={}, object_info=info
        )


def test_every_typed_field_reaches_its_exact_source_socket():
    refine = {
        "enabled": True,
        "mode": "upscale",
        "upscale_method": "lanczos",
        "passes": 4,
        "seed_mode": "offset",
        "aspect_ratio": "follow_director",
        "megapixels": 2.0,
        "width": 0,
        "height": 0,
        "skip_fl2v": False,
        "enable_latent_chunking": True,
        "enable_tiling": True,
    }
    face = {
        "enabled": True,
        "detector": "face_yolov8m.pt",
        "confidence": 0.5,
        "crop_factor": 3.0,
        "canvas_width": 640,
        "canvas_height": 960,
        "canvas_mode": "auto_capped_768",
        "select": "centre_most",
        "denoise": 0.5,
        "steps": 12,
        "seed_mode": "offset",
        "paste_region": "full_crop",
        "mask_dilation": 20,
        "feather": 30,
        "colour_match": 0.5,
        "blend": 0.8,
    }
    spec, graph = candidate(refine=refine, face_refine=face)
    built = DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})
    configs = {node["class_type"]: node["inputs"] for node in built.values()}
    for feature, values, cls in (
        ("refine", refine, "MiniMaxH3DirectorRefine"),
        ("face_refine", face, "MiniMaxH3DirectorFaceRefine"),
    ):
        for key, val in values.items():
            if key != "enabled":
                expected = "\u8ddf\u968f\u5bfc\u6f14\u53f0" if key == "aspect_ratio" else val
                assert configs[cls][key] == expected, f"{feature}.{key}"
