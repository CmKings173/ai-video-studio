"""Import disabled API templates from the approved Director UI examples.

No model execution or qualification is implied. Run with the clean pinned
Director checkout as --source and the studio workflow directory as --output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from apps.api.app.providers.minimax_h3_director.capabilities import DIRECTOR_SOURCE
from apps.api.app.providers.minimax_h3_director.config_graph import (
    LATENT_UPSCALE_MODEL,
    SCHEDULER_DEFAULTS,
    install_config_topology,
    one_node,
)
from apps.api.app.services.workflow_registry import ApprovedWorkflow

ALLOWED_CLASSES = {
    "UNETLoader",
    "CLIPLoader",
    "VAELoader",
    "MiniMaxH3Director",
    "CreateVideo",
    "SaveVideo",
    "MiniMaxH3DirectorRefine",
    "MiniMaxH3DirectorFaceRefine",
    "BasicScheduler",
}
DISPLAY_CLASSES = {"PreviewAny", "MarkdownNote"}


def validate_ui_links(value: dict, *, enforce_allowlist: bool = True) -> dict:
    nodes = {}
    for node in value["nodes"]:
        if type(node["id"]) not in {int, str} or str(node["id"]) == "":
            raise ValueError("Invalid source node id")
        key = str(node["id"])
        if key in nodes:
            raise ValueError("Duplicate source node id")
        if enforce_allowlist and node["type"] not in ALLOWED_CLASSES | DISPLAY_CLASSES:
            raise ValueError(f"Unsupported source example class: {node['type']}")
        names = [socket["name"] for socket in node.get("inputs", [])]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate source input socket")
        nodes[key] = node
    links = {}
    targets = set()
    for link in value["links"]:
        if len(link) != 6 or type(link[0]) is not int or link[0] in links:
            raise ValueError("Invalid or duplicate source link id")
        link_id, origin, origin_slot, target, target_slot, link_type = link
        source, destination = nodes.get(str(origin)), nodes.get(str(target))
        if source is None or destination is None:
            raise ValueError("Source link references an absent node")
        if (
            type(origin_slot) is not int
            or type(target_slot) is not int
            or not 0 <= origin_slot < len(source.get("outputs", []))
            or not 0 <= target_slot < len(destination.get("inputs", []))
        ):
            raise ValueError("Source link has an invalid socket index")
        output = source["outputs"][origin_slot]
        socket = destination["inputs"][target_slot]
        if socket.get("link") != link_id or link_id not in (output.get("links") or []):
            raise ValueError("Source link does not match socket declarations")
        types = {output["type"], socket["type"], link_type} - {"*"}
        if len(types) != 1:
            raise ValueError("Source link socket types disagree")
        target_key = (str(target), target_slot)
        if target_key in targets:
            raise ValueError("Multiple links target one input socket")
        targets.add(target_key)
        links[link_id] = link
    for node in nodes.values():
        for index, socket in enumerate(node.get("inputs", [])):
            link_id = socket.get("link")
            if link_id is not None:
                link = links.get(link_id)
                if link is None or str(link[3]) != str(node["id"]) or link[4] != index:
                    raise ValueError("Source example has an inconsistent input link")
        for index, socket in enumerate(node.get("outputs", [])):
            for link_id in socket.get("links") or []:
                link = links.get(link_id)
                if link is None or str(link[1]) != str(node["id"]) or link[2] != index:
                    raise ValueError("Source example has an inconsistent output link")
    return links


def api_graph(value: dict) -> dict:
    links = validate_ui_links(value)
    graph = {}
    for node in value["nodes"]:
        if node["type"] in DISPLAY_CLASSES:
            continue
        widgets = iter(node.get("widgets_values", []))
        inputs = {}
        for socket in node.get("inputs", []):
            widget_value = None
            if socket.get("widget"):
                try:
                    widget_value = next(widgets)
                except StopIteration as exc:
                    raise ValueError("Source example is missing required widget values") from exc
                if socket["name"] == "seed":
                    control = next(widgets, None)
                    if control not in {"fixed", "increment", "decrement", "randomize"}:
                        raise ValueError("Unknown Director seed control widget")
            link_id = socket.get("link")
            if link_id is not None:
                link = links[link_id]
                origin = next(item for item in value["nodes"] if str(item["id"]) == str(link[1]))
                if origin["type"] in DISPLAY_CLASSES:
                    raise ValueError("Runtime node cannot consume a discarded display node")
                inputs[socket["name"]] = [str(link[1]), link[2]]
            elif socket.get("widget"):
                inputs[socket["name"]] = widget_value
        if list(widgets):
            raise ValueError("Source example has unmapped widget values")
        graph[str(node["id"])] = {"class_type": node["type"], "inputs": inputs}
    return graph


def verify_refine_example(source: Path) -> tuple[str, str]:
    path = source / "example_workflows" / "minimax_h3_director_\u4e8c\u91c7_\u52a0\u901f.json"
    raw = path.read_bytes()
    value = json.loads(raw)
    validated_links = validate_ui_links(value, enforce_allowlist=False)
    # Extract only the audited config nodes. The accelerated example contains
    # other classes that are deliberately outside this importer's allowlist.
    selected = {}
    for class_type in ("BasicScheduler", "MiniMaxH3DirectorRefine"):
        matches = [node for node in value["nodes"] if node["type"] == class_type]
        if len(matches) != 1:
            raise ValueError(f"Source refine example requires exactly one {class_type}")
        selected[class_type] = matches[0]
    scheduler = selected["BasicScheduler"]
    refine = selected["MiniMaxH3DirectorRefine"]
    if scheduler.get("widgets_values_named") != SCHEDULER_DEFAULTS:
        raise ValueError("Source BasicScheduler defaults changed")
    named = refine.get("widgets_values_named") or {}
    if named.get("latent_upscale_model") != LATENT_UPSCALE_MODEL or named.get("sampler") != "euler":
        raise ValueError("Source refine weight/sampler defaults changed")
    links = validated_links
    sockets = {socket["name"]: socket for socket in refine["inputs"]}
    sigma_link = links[sockets["sigmas"]["link"]]
    if sigma_link[1:3] != [scheduler["id"], 0] or sigma_link[5] != "SIGMAS":
        raise ValueError("Source refine SIGMAS wiring changed")
    model_socket = next(socket for socket in scheduler["inputs"] if socket["name"] == "model")
    if links[model_socket["link"]][1:3] != links[sockets["refine_model"]["link"]][1:3]:
        raise ValueError("Source refine scheduler uses a different model")
    return path.name, hashlib.sha256(raw).hexdigest()


def import_templates(source: Path, output: Path, *, aggregate: bool = False) -> list[dict]:
    commit = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={source.as_posix()}",
            "-C",
            str(source),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    if commit != DIRECTOR_SOURCE["commit"]:
        raise ValueError("Director checkout does not match the approved source commit")
    dirty = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={source.as_posix()}",
            "-C",
            str(source),
            "status",
            "--porcelain",
        ],
        text=True,
    )
    if dirty.strip():
        raise ValueError("Director checkout must be clean before importing source templates")
    refine_example, refine_example_hash = verify_refine_example(source)
    entries = []
    graph_files = {}
    for task in ("t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"):
        source_task = "fl2v" if task == "i2v" else task
        path = source / "example_workflows" / f"minimax_h3_director_{source_task}.json"
        raw = path.read_bytes()
        graph = api_graph(json.loads(raw))
        director_id, director = one_node(graph, {"MiniMaxH3Director"})
        output_id, _ = one_node(graph, {"SaveVideo"})
        install_config_topology(graph)
        director["inputs"].update(task_type=task, global_prompt="", timeline_data="", seed=0)
        graph[output_id]["inputs"]["filename_prefix"] = "studio/director/unqualified"
        slots = {
            role: (director_id, name)
            for role, name in (
                ("PROMPT", "global_prompt"),
                ("SEED", "seed"),
                ("STEPS", "steps"),
                ("WIDTH", "width"),
                ("HEIGHT", "height"),
                ("FRAMES", "total_frames"),
            )
        }
        slots["OUTPUT_PREFIX"] = (output_id, "filename_prefix")
        version = f"director-{commit[:12]}-config-template-v2"
        if aggregate:
            graph = {
                key: node
                for key, node in graph.items()
                if node["class_type"] not in {"CreateVideo", "SaveVideo"}
            }
            director["class_type"] = "StudioMiniMaxH3Director"
            director["inputs"]["studio_output_prefix"] = "studio/director/unqualified"
            output_id = director_id
            slots["OUTPUT_PREFIX"] = (director_id, "studio_output_prefix")
            version += "-aggregate-dynamic-bridge-v1"
        execution_scope = "aggregate" if aggregate else "single_scene"
        approved = ApprovedWorkflow(task, version, graph, slots, execution_scope=execution_scope)
        approved.validate()
        suffix = "_aggregate" if aggregate else ""
        filename = f"director_{task}{suffix}.api.json"
        graph_files[filename] = json.dumps(graph, indent=2, ensure_ascii=True) + "\n"
        entries.append(
            {
                "code": f"H3_DIRECTOR_{task.upper()}_BASE{suffix.upper()}",
                "mode": task,
                "execution_scope": execution_scope,
                "version": version,
                "file": filename,
                "quality_profile": "BASE",
                "auto_approve": False,
                "slots": {key: list(value) for key, value in slots.items()},
                "required_slots": [],
                "profile": {
                    "provider": "minimax_h3_director",
                    "provider_source": DIRECTOR_SOURCE,
                    "source_example": path.name,
                    "source_example_sha256": hashlib.sha256(raw).hexdigest(),
                    "config_source_example": refine_example,
                    "config_source_example_sha256": refine_example_hash,
                    "feature_states": {
                        feature: {
                            "source_supported": True,
                            "graph_wired": True,
                            "statically_valid": True,
                            "runtime_qualified": False,
                            "advertised": False,
                        }
                        for feature in ("refine", "face_refine")
                    },
                    "quality_profile": "BASE",
                    "steps": director["inputs"]["steps"],
                    "fps": 24,
                    "cfg": director["inputs"]["cfg"],
                    "sampler": director["inputs"]["sampler"],
                    "scheduler": director["inputs"]["scheduler"],
                    "turbo": False,
                    "resolution": {"kind": "explicit_slots", "canvases": {"16:9": [864, 480]}},
                    "duration_resolver": {"kind": "director_frames"},
                    "output_node": output_id,
                    "output_artifacts": [{"role": "final", "node_id": output_id, "required": True}],
                    "workflow_hash": approved.workflow_hash,
                    "slot_map_hash": approved.slot_map_hash,
                    "dependency_versions": {},
                    "weight_hashes": {},
                    "poc_verified": False,
                    "qualification_status": "SOURCE_TEMPLATE_ONLY",
                },
            }
        )
        if aggregate:
            entries[-1]["profile"].update(
                export_mode="segments",
                bridge_version="studio-director-bridge-v1",
                output_artifacts=[
                    {
                        "role": "segment",
                        "node_id": director_id,
                        "required": True,
                        "coverage": "all_members",
                        "transport": "studio_native_segments_v1",
                    }
                ],
            )
    registry_path = output / "registry.json"
    registry = (
        json.loads(registry_path.read_text(encoding="utf-8"))
        if registry_path.exists()
        else {"schema_version": 1, "workflows": []}
    )
    identities = {entry["code"] for entry in entries}
    owned_files = {entry["file"] for entry in entries}
    registry["workflows"] = [
        entry
        for entry in registry["workflows"]
        if entry["code"] not in identities and entry.get("file") not in owned_files
    ] + entries
    # Validate every source graph and the existing registry before overwriting
    # any artifacts; a malformed later example must leave prior hashes valid.
    output.mkdir(parents=True, exist_ok=True)
    for filename, contents in graph_files.items():
        (output / filename).write_text(contents, encoding="utf-8")
    registry_path.write_text(
        json.dumps(registry, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return entries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Import a native aggregate template with dynamic member coverage",
    )
    arguments = parser.parse_args()
    imported = import_templates(
        arguments.source.resolve(),
        arguments.output.resolve(),
        aggregate=arguments.aggregate,
    )
    print(f"Imported {len(imported)} disabled Director templates")
