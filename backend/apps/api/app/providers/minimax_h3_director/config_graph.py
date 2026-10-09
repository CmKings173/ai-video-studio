"""Bounded config topology audited against Director commit a8f57b8e23c4.

These defaults describe source sockets, never installed weights or qualification.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from .graph_identity import DIRECTOR_CLASS_TYPES

FOLLOW_DIRECTOR_ASPECT = "\u8ddf\u968f\u5bfc\u6f14\u53f0"
LATENT_UPSCALE_MODEL = "minimax_h3_latent_upscaler_3d_bf16.safetensors"
CONFIG_CLASSES = {
    "refine": "MiniMaxH3DirectorRefine",
    "face_refine": "MiniMaxH3DirectorFaceRefine",
}
REFINE_DEFAULTS = {
    "mode": "refine",
    "upscale_method": "h3_latent",
    "latent_upscale_model": LATENT_UPSCALE_MODEL,
    "sampler": "euler",
    "passes": 1,
    "seed_mode": "inherit",
    "aspect_ratio": FOLLOW_DIRECTOR_ASPECT,
    "megapixels": 0.0,
    "width": 0,
    "height": 0,
    "skip_fl2v": True,
    "confirm_first_pass": False,
    "enable_latent_chunking": False,
    "enable_tiling": False,
    "tile_count": 2,
    "tile_overlap": 128,
    "seed": 0,
}
FACE_DEFAULTS = {
    "bd_grp_face_detect": "\u8138\u90e8\u68c0\u6d4b\u8bbe\u7f6e",
    "detector": "face_yolov8m.pt",
    "confidence": 0.35,
    "crop_factor": 2.5,
    "canvas_width": 768,
    "canvas_height": 768,
    "canvas_mode": "manual",
    "select": "largest_face",
    "bd_grp_face_sample": "\u91c7\u6837\u8bbe\u7f6e",
    "denoise": 0.4,
    "steps": 8,
    "sampler": "euler",
    "scheduler": "simple",
    "seed_mode": "inherit",
    "bd_grp_face_paste": "\u8d34\u56de\u8bbe\u7f6e",
    "paste_region": "face_only",
    "mask_dilation": 16,
    "feather": 24,
    "colour_match": 1.0,
    "blend": 1.0,
}
SCHEDULER_DEFAULTS = {"scheduler": "beta", "steps": 3, "denoise": 0.2}


def one_node(graph: Mapping[str, Any], classes: set[str] | frozenset[str]):
    matches = [(key, node) for key, node in graph.items() if node.get("class_type") in classes]
    if len(matches) != 1:
        raise ValueError(f"Config topology requires exactly one {sorted(classes)} node")
    return matches[0]


def api_model(feature: str):
    from ...schemas.api import DirectorFaceRefine, DirectorRefine

    return DirectorRefine if feature == "refine" else DirectorFaceRefine


def typed_config(feature: str, value: Mapping[str, Any]) -> dict[str, Any]:
    # Revalidate frozen dictionaries too: they can bypass API request validation.
    model = api_model(feature)
    try:
        config = model.model_validate(dict(value)).model_dump()
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Director.{feature} settings: {exc}") from exc
    if feature == "refine" and config["enabled"]:
        if config["aspect_ratio"] != "follow_director" or config["width"] or config["height"]:
            raise ValueError("Enabled refine supports only follow_director with width/height zero")
    if feature == "face_refine" and config["select"] not in {"largest_face", "centre_most"}:
        raise ValueError("Unsupported face selection")
    return config


def install_config_topology(graph: dict[str, Any]) -> None:
    _, director = one_node(graph, DIRECTOR_CLASS_TYPES)
    if any(
        node["class_type"] in set(CONFIG_CLASSES.values()) | {"BasicScheduler"}
        for node in graph.values()
    ):
        raise ValueError("Base template already contains config topology")
    model = director["inputs"]["model"]
    next_id = max((int(key) for key in graph if str(key).isdigit()), default=0) + 1
    scheduler_id, refine_id, face_id = (str(next_id + offset) for offset in range(3))
    graph[scheduler_id] = {
        "class_type": "BasicScheduler",
        "inputs": {**SCHEDULER_DEFAULTS, "model": list(model)},
    }
    graph[refine_id] = {
        "class_type": CONFIG_CLASSES["refine"],
        "inputs": {**REFINE_DEFAULTS, "refine_model": list(model), "sigmas": [scheduler_id, 0]},
    }
    graph[face_id] = {"class_type": CONFIG_CLASSES["face_refine"], "inputs": dict(FACE_DEFAULTS)}
    director["inputs"].update(refine=[refine_id, 0], face_refine=[face_id, 0])
    validate_config_topology(graph, director, "refine")
    validate_config_topology(graph, director, "face_refine")


def validate_config_topology(graph: Mapping[str, Any], director: dict, feature: str):
    node_id, node = one_node(graph, {CONFIG_CLASSES[feature]})
    if not output_zero(director["inputs"].get(feature), node_id):
        raise ValueError(f"Invalid Director.{feature} config output socket")
    inputs = node.get("inputs")
    defaults = REFINE_DEFAULTS if feature == "refine" else FACE_DEFAULTS
    if not isinstance(inputs, dict) or not defaults.keys() <= inputs.keys():
        raise ValueError(f"Missing required {feature} config inputs")
    if "enabled" in inputs:
        raise ValueError("Source config nodes have no enabled socket")
    extra = {"refine_model", "sigmas"} if feature == "refine" else set()
    if inputs.keys() - defaults.keys() - extra:
        raise ValueError(f"Unsupported bounded {feature} input sockets")
    settings = {
        key: value for key, value in inputs.items() if key in api_model(feature).model_fields
    }
    if feature == "refine" and settings.get("aspect_ratio") == FOLLOW_DIRECTOR_ASPECT:
        settings["aspect_ratio"] = "follow_director"
    typed_config(feature, {**settings, "enabled": True})
    for key, default in defaults.items():
        # A primitive socket cannot secretly be another node's output link.
        if type(inputs[key]) is not type(default):
            raise ValueError(f"Invalid {feature}.{key} socket value type")
    if feature == "face_refine":
        for key in (
            "sampler",
            "scheduler",
            "bd_grp_face_detect",
            "bd_grp_face_sample",
            "bd_grp_face_paste",
        ):
            if inputs[key] != defaults[key]:
                raise ValueError(f"Invalid fixed face input {key}")
        if "sigmas" in inputs:
            raise ValueError("Bounded face topology uses its internal scheduler")
        return node_id, node
    for key in (
        "latent_upscale_model",
        "sampler",
        "confirm_first_pass",
        "tile_count",
        "tile_overlap",
    ):
        if inputs[key] != defaults[key]:
            raise ValueError(f"Invalid fixed refine input {key}")
    if not 0 <= inputs["seed"] <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("Refine seed exceeds the source unsigned integer range")
    scheduler_id, scheduler = one_node(graph, {"BasicScheduler"})
    if not output_zero(inputs.get("sigmas"), scheduler_id):
        raise ValueError("Refine requires BasicScheduler SIGMAS output zero")
    scheduler_inputs = scheduler.get("inputs") or {}
    if set(scheduler_inputs) != set(SCHEDULER_DEFAULTS) | {"model"} or any(
        scheduler_inputs.get(key) != val or type(scheduler_inputs.get(key)) is not type(val)
        for key, val in SCHEDULER_DEFAULTS.items()
    ):
        raise ValueError("Invalid source BasicScheduler inputs")
    model = inputs.get("refine_model", director["inputs"].get("model"))
    if scheduler_inputs.get("model") != model or model != director["inputs"].get("model"):
        raise ValueError("Refine scheduler must use the Director sampling model")
    if (
        not isinstance(model, list)
        or len(model) != 2
        or type(model[1]) is not int
        or model[1] != 0
        or graph.get(str(model[0]), {}).get("class_type") != "UNETLoader"
    ):
        raise ValueError("Invalid MODEL source socket")
    return node_id, node


def validate_object_types(
    object_info: Mapping[str, Any] | None, director_class: str, feature: str
) -> None:
    if object_info is None:
        return
    expected = "MMX_DIR_REFINE" if feature == "refine" else "MMX_DIR_FACE_REFINE"
    config_info = object_info.get(CONFIG_CLASSES[feature], {})
    if not config_info.get("output") or config_info["output"][0] != expected:
        raise ValueError(f"Installed {feature} config output type differs from source")
    director_inputs = object_info.get(director_class, {}).get("input") or {}
    socket = next(
        (
            director_inputs.get(group, {}).get(feature)
            for group in ("required", "optional")
            if feature in director_inputs.get(group, {})
        ),
        None,
    )
    if not socket or socket[0] != expected:
        raise ValueError(f"Installed Director.{feature} socket type differs from source")
    if feature == "refine":
        scheduler = object_info.get("BasicScheduler", {})
        if not scheduler.get("output") or scheduler["output"][0] != "SIGMAS":
            raise ValueError("Installed BasicScheduler has no SIGMAS output zero")
        groups = config_info.get("input") or {}
        sigmas = next(
            (
                groups.get(group, {}).get("sigmas")
                for group in ("required", "optional")
                if "sigmas" in groups.get(group, {})
            ),
            None,
        )
        model = (scheduler.get("input") or {}).get("required", {}).get("model")
        if not sigmas or sigmas[0] != "SIGMAS" or not model or model[0] != "MODEL":
            raise ValueError("Installed refine scheduler input types differ from source")
        refine_model = next(
            (
                groups.get(group, {}).get("refine_model")
                for group in ("required", "optional")
                if "refine_model" in groups.get(group, {})
            ),
            None,
        )
        loader = object_info.get("UNETLoader", {})
        if (
            not refine_model
            or refine_model[0] != "MODEL"
            or not loader.get("output")
            or loader["output"][0] != "MODEL"
        ):
            raise ValueError("Installed refine MODEL socket types differ from source")


def output_zero(value: Any, node_id: str) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and value[0] == node_id
        and type(value[1]) is int
        and value[1] == 0
    )
