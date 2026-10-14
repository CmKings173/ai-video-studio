from copy import deepcopy

import pytest

from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec, stable_hash
from apps.api.app.providers.minimax_h3_director.workflow_builder import (
    DirectorWorkflowBuilder,
    DirectorWorkflowError,
)
from tests.unit.test_director_execution_contract import candidate

OUTPUT_TYPES = {
    "UNETLoader": ["MODEL"],
    "CLIPLoader": ["CLIP"],
    "VAELoader": ["VAE"],
    "MiniMaxH3Director": [
        "IMAGE",
        "AUDIO",
        "FLOAT",
        "IMAGE",
        "IMAGE",
        "STRING",
        "IMAGE",
        "IMAGE",
    ],
    "CreateVideo": ["VIDEO"],
    "SaveVideo": [],
    "BasicScheduler": ["SIGMAS"],
    "MiniMaxH3DirectorRefine": ["MMX_DIR_REFINE", "INT", "INT"],
    "MiniMaxH3DirectorFaceRefine": ["MMX_DIR_FACE_REFINE"],
}


def installed_object_info(graph):
    info = {}
    for node in graph.values():
        class_type = node["class_type"]
        inputs = {}
        for name, value in node["inputs"].items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and type(value[1]) is int
            ):
                source = graph[value[0]]
                kind = OUTPUT_TYPES[source["class_type"]][value[1]]
            elif isinstance(value, str):
                kind = "STRING"
            elif type(value) is bool:
                kind = "BOOLEAN"
            elif type(value) is int:
                kind = "INT"
            elif type(value) is float:
                kind = "FLOAT"
            else:
                kind = "*"
            inputs[name] = [kind, {}]
        info[class_type] = {
            "input": {"required": inputs},
            "output": OUTPUT_TYPES[class_type],
        }
    return info


def spec_for_graph(spec, graph):
    return DirectorExecutionSpec.finalize(
        **{**spec.model_dump(), "workflow_hash": stable_hash(graph)}
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_exporter_class",
        "missing_exporter_input",
        "missing_required_exporter_input",
        "unsupported_exporter_choice",
        "wrong_video_socket_type",
    ],
)
def test_installed_object_info_covers_active_exporter_contract(damage):
    spec, graph = candidate()
    info = installed_object_info(graph)
    if damage == "missing_exporter_class":
        del info["SaveVideo"]
    elif damage == "missing_exporter_input":
        del info["SaveVideo"]["input"]["required"]["video"]
    elif damage == "missing_required_exporter_input":
        info["CreateVideo"]["input"]["required"]["video"] = ["VIDEO", {}]
    elif damage == "unsupported_exporter_choice":
        info["SaveVideo"]["input"]["required"]["format"] = [["webm"], {}]
    else:
        info["SaveVideo"]["input"]["required"]["video"] = ["IMAGE", {}]

    with pytest.raises(DirectorWorkflowError):
        DirectorWorkflowBuilder().build(
            base_workflow=graph,
            spec=spec,
            staged_assets={},
            object_info=info,
        )


def test_installed_object_info_requires_connected_source_output_metadata():
    spec, graph = candidate()
    info = installed_object_info(graph)
    del info["UNETLoader"]["output"]

    with pytest.raises(DirectorWorkflowError, match="has no output metadata"):
        DirectorWorkflowBuilder().build(
            base_workflow=graph,
            spec=spec,
            staged_assets={},
            object_info=info,
        )


def test_static_build_without_object_info_is_deterministic_and_non_mutating():
    spec, template = candidate()
    original = deepcopy(template)

    first = DirectorWorkflowBuilder().build(
        base_workflow=template,
        spec=spec,
        staged_assets={},
    )
    second = DirectorWorkflowBuilder().build(
        base_workflow=template,
        spec=spec,
        staged_assets={},
    )

    assert first == second
    assert template == original


def test_static_build_rejects_dangling_graph_links():
    spec, graph = candidate()
    director = next(node for node in graph.values() if node["class_type"] == "MiniMaxH3Director")
    director["inputs"]["model"] = ["missing-node", 0]
    spec = spec_for_graph(spec, graph)

    with pytest.raises(DirectorWorkflowError, match="references missing node"):
        DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})


@pytest.mark.parametrize(
    ("input_name", "wrong_source_id"),
    [("model", "2"), ("video_vae", "2"), ("audio_vae", "2"), ("clip", "1")],
)
def test_static_build_rejects_director_dependency_wired_to_wrong_class(
    input_name, wrong_source_id
):
    spec, graph = candidate()
    director = next(node for node in graph.values() if node["class_type"] == "MiniMaxH3Director")
    director["inputs"][input_name] = [wrong_source_id, 0]
    spec = spec_for_graph(spec, graph)

    with pytest.raises(DirectorWorkflowError, match="must be wired to"):
        DirectorWorkflowBuilder().build(base_workflow=graph, spec=spec, staged_assets={})
