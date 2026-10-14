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


def _validate_graph_links(graph: Mapping[str, Any]) -> None:
    for node_id, node in graph.items():
        if not isinstance(node, Mapping) or not isinstance(node.get("class_type"), str):
            raise DirectorWorkflowError(f"Director graph node {node_id} has no class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, Mapping):
            raise DirectorWorkflowError(f"Director graph node {node_id} has no inputs object")
        for input_name, value in inputs.items():
            if not (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
            ):
                continue
            if type(value[1]) is not int or value[1] < 0:
                raise DirectorWorkflowError(f"Invalid ComfyUI link on {node_id}.{input_name}")
            if value[0] not in graph:
                raise DirectorWorkflowError(
                    f"ComfyUI link on {node_id}.{input_name} references missing node {value[0]}"
                )


def _validate_director_dependencies(
    graph: Mapping[str, Any], director: Mapping[str, Any]
) -> None:
    inputs = director.get("inputs")
    if not isinstance(inputs, Mapping):
        raise DirectorWorkflowError("Director node has no inputs object")
    expected_classes = {
        "model": "UNETLoader",
        "video_vae": "VAELoader",
        "audio_vae": "VAELoader",
        "clip": "CLIPLoader",
    }
    for name, class_type in expected_classes.items():
        link = inputs.get(name)
        if (
            not isinstance(link, list)
            or len(link) != 2
            or not isinstance(link[0], str)
            or type(link[1]) is not int
            or link[1] != 0
        ):
            raise DirectorWorkflowError(f"Director.{name} must bind output zero of {class_type}")
        source = graph.get(link[0])
        if not isinstance(source, Mapping) or source.get("class_type") != class_type:
            raise DirectorWorkflowError(
                f"Director.{name} must be wired to a {class_type} node"
            )


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


def _validate_installed_graph(
    graph: Mapping[str, Any],
    object_info: Mapping[str, Any],
    *,
    require_required_inputs: bool,
    validate_connections: bool,
) -> None:
    schemas: dict[str, Mapping[str, Any]] = {}
    input_schemas: dict[str, dict[str, Any]] = {}

    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            raise DirectorWorkflowError(f"Director graph node {node_id} is malformed")
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, Mapping):
            raise DirectorWorkflowError(f"Director graph node {node_id} is not an API node")
        schema = object_info.get(class_type)
        if not isinstance(schema, Mapping):
            raise DirectorWorkflowError(f"ComfyUI object_info is missing {class_type}")
        groups = schema.get("input")
        if not isinstance(groups, Mapping):
            raise DirectorWorkflowError(f"ComfyUI object_info has no input schema for {class_type}")

        descriptors: dict[str, Any] = {}
        required: set[str] = set()
        for group in ("required", "optional", "hidden"):
            fields = groups.get(group) or {}
            if not isinstance(fields, Mapping):
                raise DirectorWorkflowError(
                    f"ComfyUI object_info has an invalid {group} input schema for {class_type}"
                )
            descriptors.update(fields)
            if group == "required":
                required.update(str(name) for name in fields)

        unknown = set(inputs) - set(descriptors)
        if unknown:
            raise DirectorWorkflowError(
                f"ComfyUI object_info does not expose {class_type} input(s): "
                f"{', '.join(sorted(unknown))}"
            )
        if require_required_inputs:
            missing = required - set(inputs)
            if missing:
                raise DirectorWorkflowError(
                    f"Director graph node {node_id} is missing required installed input(s): "
                    f"{', '.join(sorted(missing))}"
                )

        schemas[str(node_id)] = schema
        input_schemas[str(node_id)] = descriptors

    if not validate_connections:
        return

    for node_id, node in graph.items():
        inputs = node["inputs"]
        descriptors = input_schemas[str(node_id)]
        for name, value in inputs.items():
            if not (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in graph
            ):
                continue
            if type(value[1]) is not int:
                raise DirectorWorkflowError(f"Invalid ComfyUI link on {node_id}.{name}")

            source_id, output_index = value
            source_schema = schemas[source_id]
            outputs = source_schema.get("output")
            if not isinstance(outputs, list):
                raise DirectorWorkflowError(
                    "ComfyUI object_info has no output metadata for "
                    f"{graph[source_id]['class_type']}"
                )
            if not 0 <= output_index < len(outputs):
                raise DirectorWorkflowError(
                    f"ComfyUI object_info has no output {output_index} for "
                    f"{graph[source_id]['class_type']}"
                )

            descriptor = descriptors.get(name)
            if not isinstance(descriptor, (list, tuple)) or not descriptor:
                raise DirectorWorkflowError(
                    f"ComfyUI object_info has no type for {node['class_type']}.{name}"
                )
            input_type = descriptor[0]
            output_type = outputs[output_index]
            if (
                not isinstance(input_type, str)
                or not isinstance(output_type, str)
                or (input_type != output_type and input_type != "*" and output_type != "*")
            ):
                raise DirectorWorkflowError(
                    f"ComfyUI socket type mismatch for {node['class_type']}.{name}"
                )

    for node_id, node in graph.items():
        descriptors = input_schemas[str(node_id)]
        for name, value in node["inputs"].items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and value[0] in graph
            ):
                continue
            descriptor = descriptors.get(name)
            if not isinstance(descriptor, (list, tuple)) or not descriptor:
                raise DirectorWorkflowError(
                    f"ComfyUI object_info has no input descriptor for {node['class_type']}.{name}"
                )
            choices = descriptor[0]
            if isinstance(choices, list) and value not in choices:
                raise DirectorWorkflowError(
                    f"Installed {node['class_type']}.{name} does not support {value!r}"
                )


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
        _validate_graph_links(graph)
        if object_info is not None:
            if not isinstance(object_info, Mapping):
                raise DirectorWorkflowError("ComfyUI object_info must be an object")
            _validate_installed_graph(
                graph,
                object_info,
                require_required_inputs=True,
                validate_connections=False,
            )
        _node_id, director = _find_one(graph, DIRECTOR_CLASS_TYPES)
        _validate_director_dependencies(graph, director)
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
        if object_info is not None:
            _validate_installed_graph(
                graph,
                object_info,
                require_required_inputs=False,
                validate_connections=True,
            )
        return graph
