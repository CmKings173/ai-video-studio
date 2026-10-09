from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from .config_graph import (
    CONFIG_CLASSES,
    FOLLOW_DIRECTOR_ASPECT,
    typed_config,
    validate_config_topology,
    validate_object_types,
)
from .contracts import DirectorExecutionSpec, artifact_member_range
from .graph_identity import DIRECTOR_CLASS_TYPES
from .timeline import build_timeline


class DirectorWorkflowError(ValueError):
    pass


def _find_one(
    graph: Mapping[str, Any], class_types: set[str] | frozenset[str]
) -> tuple[str, dict[str, Any]]:
    matches = [
        (str(node_id), node)
        for node_id, node in graph.items()
        if isinstance(node, dict) and node.get("class_type") in class_types
    ]
    if len(matches) != 1:
        raise DirectorWorkflowError(
            f"Qualified Director graph must contain exactly one {sorted(class_types)} node"
        )
    return matches[0]


def _object_inputs(object_info: Mapping[str, Any] | None, class_type: str) -> set[str] | None:
    if object_info is None:
        return None
    info = object_info.get(class_type)
    if not isinstance(info, Mapping):
        raise DirectorWorkflowError(f"ComfyUI object_info is missing {class_type}")
    inputs = info.get("input") or {}
    names: set[str] = set()
    for group in ("required", "optional", "hidden"):
        values = inputs.get(group) or {}
        if isinstance(values, Mapping):
            names.update(str(name) for name in values)
    return names


def _patch_existing(
    node: dict[str, Any], values: Mapping[str, Any], allowed: set[str] | None, *, required: set[str]
) -> None:
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise DirectorWorkflowError("Director node has no inputs object")
    for name in required:
        if name not in inputs:
            raise DirectorWorkflowError(f"Qualified Director template is missing input {name}")
        if allowed is not None and name not in allowed:
            raise DirectorWorkflowError(
                f"ComfyUI object_info does not expose Director input {name}"
            )
    for name, value in values.items():
        if name not in inputs:
            if allowed is None or name not in allowed:
                raise DirectorWorkflowError(f"Qualified template cannot bind input {name}")
        if allowed is not None and name not in allowed:
            raise DirectorWorkflowError(
                f"ComfyUI object_info does not expose Director input {name}"
            )
        inputs[name] = value


def _combo_value(
    object_info: Mapping[str, Any] | None, class_type: str, name: str, value: Any
) -> Any:
    # Source alias from director/refine_pack.py at the pinned commit.
    if (
        class_type == "MiniMaxH3DirectorRefine"
        and name == "aspect_ratio"
        and value == "follow_director"
    ):
        value = FOLLOW_DIRECTOR_ASPECT
    if object_info is None:
        return value
    groups = object_info[class_type].get("input") or {}
    descriptor = next(
        (
            groups.get(group, {}).get(name)
            for group in ("required", "optional")
            if name in groups.get(group, {})
        ),
        None,
    )
    if not descriptor or not isinstance(descriptor[0], list):
        return value
    choices = descriptor[0]
    if value in choices:
        return value
    # The pinned task combo uses 'key - localized label'. Resolve only that
    # source-defined decoration; never guess a label from a translation.
    matches = [
        choice
        for choice in choices
        if isinstance(choice, str) and choice.split(" - ", 1)[0] == value
    ]
    if len(matches) != 1:
        raise DirectorWorkflowError(f"Installed {class_type}.{name} does not support {value!r}")
    return matches[0]


def validate_export_bindings(graph: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    director_id, _ = _find_one(graph, DIRECTOR_CLASS_TYPES)
    bindings = profile.get("output_artifacts")
    if bindings is None:
        bindings = [{"role": "final", "node_id": profile.get("output_node")}]
    if not isinstance(bindings, list) or not bindings:
        raise DirectorWorkflowError("Director requires an explicit exporter manifest")
    image_slots = {"final": 0, "segment": 0, "pre-refine": 6, "pre-face": 7}
    for binding in bindings:
        if not isinstance(binding, Mapping):
            raise DirectorWorkflowError("Invalid Director exporter binding")
        exporter = graph.get(str(binding.get("node_id")))
        if not isinstance(exporter, Mapping):
            raise DirectorWorkflowError("Director exporter is absent from the qualified graph")
        role = binding.get("role")
        if "coverage" in binding:
            try:
                artifact_member_range(dict(binding), 1)
            except ValueError as exc:
                raise DirectorWorkflowError(str(exc)) from exc
        if binding.get("transport") == "studio_native_segments_v1":
            if (
                str(binding.get("node_id")) != director_id
                or exporter.get("class_type") != "StudioMiniMaxH3Director"
                or role not in {"segment", "pre-refine", "pre-face"}
                or profile.get("export_mode") != "segments"
            ):
                raise DirectorWorkflowError("Native artifacts require the studio Director bridge")
            continue
        inputs = exporter.get("inputs") or {}
        if role == "report":
            if exporter.get("class_type") != "PreviewAny" or inputs.get("source") != [
                director_id,
                5,
            ]:
                raise DirectorWorkflowError(
                    "Report exporter must consume the Director report socket"
                )
            continue
        if role not in image_slots or exporter.get("class_type") != "SaveVideo":
            raise DirectorWorkflowError("Video artifacts require a qualified SaveVideo exporter")
        connection = inputs.get("video")
        if not isinstance(connection, list) or len(connection) != 2 or connection[1] != 0:
            raise DirectorWorkflowError("Video exporter must consume a CreateVideo output")
        encoder = graph.get(str(connection[0]), {})
        encoder_inputs = encoder.get("inputs") or {}
        if (
            encoder.get("class_type") != "CreateVideo"
            or encoder_inputs.get("images") != [director_id, image_slots[role]]
            or encoder_inputs.get("audio") != [director_id, 1]
            or encoder_inputs.get("fps") != [director_id, 2]
        ):
            raise DirectorWorkflowError(
                "Artifact exporter does not consume the declared Director AV sockets"
            )
        if role == "segment" and profile.get("export_mode") != "segments":
            raise DirectorWorkflowError(
                "Segment artifacts require a qualified segments export policy"
            )


class DirectorWorkflowBuilder:
    """Patch a qualified Director template by source input names, never by guessed node ids."""

    def build(
        self,
        *,
        base_workflow: Mapping[str, Any],
        spec: DirectorExecutionSpec,
        staged_assets: dict[tuple[str, int], str],
        object_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        graph = copy.deepcopy(dict(base_workflow))
        _node_id, director = _find_one(graph, DIRECTOR_CLASS_TYPES)
        class_type = str(director["class_type"])
        allowed = _object_inputs(object_info, class_type)
        if any(
            director["inputs"].get(socket) is not None for socket in ("i2v_groups", "r2v_groups")
        ):
            raise DirectorWorkflowError(
                "Timeline-driven templates cannot retain external group inputs"
            )
        timeline = build_timeline(spec, staged_assets)
        profile = spec.provenance.get("workflow_profile") or {}
        validate_export_bindings(graph, profile)
        for binding in profile.get("output_artifacts", []):
            if "coverage" in binding:
                artifact_member_range(binding, spec.member_count)
        timeline["output"]["exportMode"] = profile.get("export_mode", "all")
        _patch_existing(
            director,
            {
                "task_type": _combo_value(object_info, class_type, "task_type", spec.task),
                "global_prompt": spec.prompt,
                "cfg": spec.cfg,
                "seed": spec.seed,
                "frame_rate": float(spec.fps),
                "width": spec.canvas.width,
                "height": spec.canvas.height,
                "ref_max_size": max(spec.canvas.width, spec.canvas.height),
                "total_frames": spec.frames,
                "timeline_data": json.dumps(timeline, ensure_ascii=False, separators=(",", ":")),
                "steps": spec.steps,
            },
            allowed,
            required={
                "task_type",
                "global_prompt",
                "cfg",
                "seed",
                "frame_rate",
                "width",
                "height",
                "ref_max_size",
                "total_frames",
                "timeline_data",
                "steps",
            },
        )
        try:
            for feature, values in (("refine", spec.refine), ("face_refine", spec.face_refine)):
                config = typed_config(feature, values)
                config_class = CONFIG_CLASSES[feature]
                matches = [
                    node for node in graph.values() if node.get("class_type") == config_class
                ]
                if len(matches) > 1:
                    raise ValueError(f"Multiple {config_class} nodes")
                if not config.pop("enabled"):
                    director["inputs"].pop(feature, None)
                    continue
                _, node = validate_config_topology(graph, director, feature)
                validate_object_types(object_info, class_type, feature)
                config_allowed = _object_inputs(object_info, config_class)
                if allowed is not None and feature not in allowed:
                    raise ValueError(f"Director object_info does not expose {feature}")
                if feature == "refine":
                    config["seed"] = spec.seed
                    scheduler_allowed = _object_inputs(object_info, "BasicScheduler")
                    if (
                        scheduler_allowed is not None
                        and not {"model", "scheduler", "steps", "denoise"} <= scheduler_allowed
                    ):
                        raise ValueError("BasicScheduler object_info is missing required inputs")
                patched = {
                    key: _combo_value(object_info, config_class, key, value)
                    for key, value in config.items()
                }
                _patch_existing(node, patched, config_allowed, required=set(node["inputs"]))
        except ValueError as exc:
            raise DirectorWorkflowError(str(exc)) from exc
        output_id = profile.get("output_node")
        if output_id is not None:
            output = graph.get(str(output_id))
            prefix_field = (
                "studio_output_prefix"
                if class_type == "StudioMiniMaxH3Director" and str(output_id) == _node_id
                else "filename_prefix"
            )
            if not isinstance(output, dict) or prefix_field not in output.get("inputs", {}):
                raise DirectorWorkflowError("Qualified output node has no filename_prefix binding")
            output["inputs"][prefix_field] = spec.output_prefix
        for binding in profile.get("output_artifacts", []):
            if binding.get("transport") == "studio_native_segments_v1":
                director["inputs"]["studio_output_prefix"] = spec.output_prefix
                continue
            if binding.get("role") == "report":
                continue
            exporter = graph[str(binding["node_id"])]
            exporter["inputs"]["filename_prefix"] = f"{spec.output_prefix}/{binding['role']}"
        return graph
