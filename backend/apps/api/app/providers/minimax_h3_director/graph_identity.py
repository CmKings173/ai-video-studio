"""Canonical identity for Director graphs, including native transport wrappers."""

from collections.abc import Mapping
from typing import Any

DIRECTOR_CLASS_TYPES = frozenset(
    {"MiniMaxH3Director", "ComfyMiniMaxH3Director", "StudioMiniMaxH3Director"}
)


def is_director_graph(graph: Mapping[str, Any]) -> bool:
    return any(
        isinstance(node, Mapping) and node.get("class_type") in DIRECTOR_CLASS_TYPES
        for node in graph.values()
    )
