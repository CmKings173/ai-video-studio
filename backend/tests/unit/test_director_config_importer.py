import json
from copy import deepcopy
from pathlib import Path

import pytest

from apps.api.app.providers.minimax_h3_director.config_graph import validate_config_topology
from apps.api.app.providers.minimax_h3_director.graph_identity import DIRECTOR_CLASS_TYPES
from apps.api.app.providers.minimax_h3_director.workflow_builder import validate_export_bindings
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from apps.api.scripts import import_director_templates as importer


def ui_graph():
    return {
        "nodes": [
            {
                "id": 1,
                "type": "UNETLoader",
                "inputs": [],
                "outputs": [{"type": "MODEL", "links": [10]}],
            },
            {
                "id": 5,
                "type": "MiniMaxH3Director",
                "inputs": [{"name": "model", "type": "MODEL", "link": 10}],
                "outputs": [],
            },
        ],
        "links": [[10, 1, 0, 5, 0, "MODEL"]],
    }


@pytest.mark.parametrize(
    "damage",
    [
        "node_id",
        "string_id_alias",
        "link_id",
        "target_slot",
        "source_slot",
        "source_type",
        "link_type",
        "target_type",
        "absent_source",
        "absent_link",
        "duplicate_socket",
        "unknown_class",
        "output_declaration",
        "input_declaration",
        "missing_widget",
    ],
)
def test_ui_import_fails_closed_on_ambiguous_or_invalid_links(damage):
    value = ui_graph()
    if damage == "node_id":
        value["nodes"].append(deepcopy(value["nodes"][0]))
    elif damage == "string_id_alias":
        duplicate = deepcopy(value["nodes"][0])
        duplicate["id"] = "1"
        value["nodes"].append(duplicate)
    elif damage == "link_id":
        value["links"].append(deepcopy(value["links"][0]))
    elif damage == "target_slot":
        value["links"][0][4] = 1
    elif damage == "source_slot":
        value["links"][0][2] = 1
    elif damage == "source_type":
        value["nodes"][0]["outputs"][0]["type"] = "CLIP"
    elif damage == "target_type":
        value["nodes"][1]["inputs"][0]["type"] = "SIGMAS"
    elif damage == "link_type":
        value["links"][0][5] = "SIGMAS"
    elif damage == "absent_source":
        value["links"][0][1] = 999
    elif damage == "absent_link":
        value["links"] = []
    elif damage == "duplicate_socket":
        value["nodes"][1]["inputs"].append(deepcopy(value["nodes"][1]["inputs"][0]))
    elif damage == "unknown_class":
        value["nodes"][0]["type"] = "ArbitraryModelLoader"
    elif damage == "output_declaration":
        value["nodes"][0]["outputs"][0]["links"] = []
    elif damage == "input_declaration":
        value["nodes"][1]["inputs"][0]["link"] = None
    elif damage == "missing_widget":
        value["nodes"][0]["inputs"] = [{"name": "unet_name", "widget": {"name": "unet_name"}}]
    with pytest.raises(ValueError):
        importer.api_graph(value)


def test_ui_import_is_deterministic_and_accepts_only_explicit_config_classes():
    value = ui_graph()
    for index, cls in enumerate(
        ("BasicScheduler", "MiniMaxH3DirectorRefine", "MiniMaxH3DirectorFaceRefine"), 20
    ):
        value["nodes"].append({"id": index, "type": cls, "inputs": [], "outputs": []})
    graph = importer.api_graph(value)
    value["nodes"].reverse()
    assert importer.api_graph(value) == graph
    assert graph["5"]["inputs"]["model"] == ["1", 0]


def source_checkout(tmp_path, monkeypatch):
    source = tmp_path / "source"
    examples = source / "example_workflows"
    examples.mkdir(parents=True)
    base = ui_graph()
    names = ["steps", "cfg", "sampler", "scheduler", "width", "height", "total_frames"]
    base["nodes"][1]["inputs"] += [
        {"name": name, "type": "COMBO", "widget": {"name": name}} for name in names
    ]
    base["nodes"][1]["widgets_values"] = [25, 1.0, "euler", "simple", 864, 480, 124]
    base["nodes"].append(
        {
            "id": 7,
            "type": "SaveVideo",
            "inputs": [{"name": "filename_prefix", "widget": {"name": "filename_prefix"}}],
            "widgets_values": ["output"],
        }
    )
    for task in ("t2v", "fl2v", "r2v", "v2v", "rv2v"):
        (examples / f"minimax_h3_director_{task}.json").write_text(
            json.dumps(base), encoding="utf-8"
        )
    refine = {
        "nodes": [
            {
                "id": 1,
                "type": "UNETLoader",
                "inputs": [],
                "outputs": [{"type": "MODEL", "links": [1, 2]}],
            },
            {
                "id": 2,
                "type": "BasicScheduler",
                "widgets_values_named": {"scheduler": "beta", "steps": 3, "denoise": 0.2},
                "inputs": [{"name": "model", "type": "MODEL", "link": 1}],
                "outputs": [{"type": "SIGMAS", "links": [3]}],
            },
            {
                "id": 3,
                "type": "MiniMaxH3DirectorRefine",
                "widgets_values_named": {
                    "latent_upscale_model": "minimax_h3_latent_upscaler_3d_bf16.safetensors",
                    "sampler": "euler",
                },
                "inputs": [
                    {"name": "refine_model", "type": "MODEL", "link": 2},
                    {"name": "sigmas", "type": "SIGMAS", "link": 3},
                ],
                "outputs": [],
            },
        ],
        "links": [[1, 1, 0, 2, 0, "MODEL"], [2, 1, 0, 3, 0, "MODEL"], [3, 2, 0, 3, 1, "SIGMAS"]],
    }
    (examples / "minimax_h3_director_\u4e8c\u91c7_\u52a0\u901f.json").write_text(
        json.dumps(refine), encoding="utf-8"
    )
    monkeypatch.setattr(
        importer.subprocess,
        "check_output",
        lambda args, **kwargs: importer.DIRECTOR_SOURCE["commit"] if args[-1] == "HEAD" else "",
    )
    return source


def test_importer_revises_twelve_templates_without_variants_or_execution_evidence(
    tmp_path, monkeypatch
):
    source = source_checkout(tmp_path, monkeypatch)
    output = tmp_path / "output"
    output.mkdir()
    unrelated = {"code": "unrelated", "version": "keep", "custom": {"preserve": True}}
    stale = {"code": "H3_DIRECTOR_T2V_BASE", "version": "old-template", "auto_approve": True}
    stale_alias = {"code": "old-alias", "file": "director_t2v.api.json", "version": "old-template"}
    (output / "registry.json").write_text(
        json.dumps({"schema_version": 1, "workflows": [unrelated, stale, stale_alias]}),
        encoding="utf-8",
    )
    for aggregate in (False, True):
        entries = importer.import_templates(source, output, aggregate=aggregate)
        assert importer.import_templates(source, output, aggregate=aggregate) == entries
    registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
    assert registry["workflows"][0] == unrelated
    assert len(registry["workflows"]) == 13
    for entry in registry["workflows"][1:]:
        graph = json.loads((output / entry["file"]).read_text(encoding="utf-8"))
        approved = ApprovedWorkflow.from_manifest({**entry, "workflow": graph})
        assert entry["profile"]["workflow_hash"] == approved.workflow_hash
        assert entry["profile"]["slot_map_hash"] == approved.slot_map_hash
        classes = [node["class_type"] for node in graph.values()]
        for cls in ("BasicScheduler", "MiniMaxH3DirectorRefine", "MiniMaxH3DirectorFaceRefine"):
            assert classes.count(cls) == 1
        assert entry["auto_approve"] is False
        assert entry["profile"]["poc_verified"] is False
        assert entry["profile"]["qualification_status"] == "SOURCE_TEMPLATE_ONLY"
        assert entry["profile"]["weight_hashes"] == {}
        assert entry["profile"]["dependency_versions"] == {}
        for state in entry["profile"]["feature_states"].values():
            assert state == {
                "source_supported": True,
                "graph_wired": True,
                "statically_valid": True,
                "runtime_qualified": False,
                "advertised": False,
            }
        scope = entry["execution_scope"]
        assert scope in {"single_scene", "aggregate"}
        if scope == "aggregate":
            assert entry["profile"]["output_artifacts"][0]["coverage"] == "all_members"


def test_registry_graphs_have_updated_hashes_and_remain_unqualified():
    directory = Path(__file__).resolve().parents[2] / "workflows" / "h3"
    entries = json.loads((directory / "registry.json").read_text(encoding="utf-8"))["workflows"]
    director_entries = [entry for entry in entries if entry["code"].startswith("H3_DIRECTOR_")]
    assert len(director_entries) == 12
    for entry in director_entries:
        graph = json.loads((directory / entry["file"]).read_text(encoding="utf-8"))
        approved = ApprovedWorkflow.from_manifest({**entry, "workflow": graph})
        assert approved.workflow_hash == entry["profile"]["workflow_hash"]
        assert approved.slot_map_hash == entry["profile"]["slot_map_hash"]
        director = next(
            node for node in graph.values() if node["class_type"] in DIRECTOR_CLASS_TYPES
        )
        for feature in ("refine", "face_refine"):
            validate_config_topology(graph, director, feature)
        validate_export_bindings(graph, entry["profile"])
        assert "config-template-v2" in entry["version"]
        assert entry["auto_approve"] is False


def test_invalid_later_example_preserves_previous_graph_and_registry(tmp_path, monkeypatch):
    source = source_checkout(tmp_path, monkeypatch)
    invalid = source / "example_workflows" / "minimax_h3_director_rv2v.json"
    value = json.loads(invalid.read_text(encoding="utf-8"))
    value["links"][0][4] = 999
    invalid.write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    graph_path = output / "director_t2v.api.json"
    graph_path.write_text("existing graph", encoding="utf-8")
    registry_path = output / "registry.json"
    registry_path.write_text('{"workflows": []}', encoding="utf-8")
    with pytest.raises(ValueError):
        importer.import_templates(source, output)
    assert graph_path.read_text(encoding="utf-8") == "existing graph"
    assert registry_path.read_text(encoding="utf-8") == '{"workflows": []}'
