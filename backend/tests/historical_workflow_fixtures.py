"""Frozen legacy manifest evidence, never imported by runtime seeding or routing."""

import copy
import json
from pathlib import Path


def historical_manifests():
    archive = Path(__file__).resolve().parent / "fixtures/historical_h3"
    registry = json.loads((archive / "registry.json").read_text())
    result = copy.deepcopy(registry["workflows"])
    for entry in result:
        graph = json.loads((archive / entry["file"]).read_text())
        derive = entry.get("derive") or {}
        if derive.get("type") == "last_only":
            inputs = graph[str(derive["target_node"])]["inputs"]
            inputs["last_frame"] = inputs.pop("first_frame")
        elif derive.get("type") == "first_last":
            loader = str(derive["loader_node"])
            graph[loader] = {
                "class_type": "LoadImage",
                "inputs": {"image": "last-frame.png"},
                "_meta": {"title": "Last Frame"},
            }
            graph[str(derive["target_node"])]["inputs"]["last_frame"] = [loader, 0]
        entry["workflow"] = graph
    return result
