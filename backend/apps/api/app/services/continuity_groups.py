"""Canonical native continuity dependencies in the current ordered storyboard."""

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene


def continuity_chains(scenes: list[Scene]) -> list[list[Scene]]:
    chains: list[list[Scene]] = []
    for scene in sorted(
        (row for row in scenes if row.enabled), key=lambda row: (row.scene_order, row.id)
    ):
        if (
            chains
            and (scene.spec or {}).get("continuity") == "CONTINUOUS"
            and chains[-1][-1].scene_order == scene.scene_order - 1
        ):
            chains[-1].append(scene)
        else:
            chains.append([scene])
    return chains


def expand_continuity_selection(selected: list[Scene], all_scenes: list[Scene]) -> list[Scene]:
    """Include native prerequisites and affected successors whenever one member is stale."""
    ids = {scene.id for scene in selected}
    return [
        scene
        for chain in continuity_chains(all_scenes)
        if any(member.id in ids for member in chain)
        for scene in chain
    ]


def requires_native_execution_group(scene: Scene, all_scenes: list[Scene]) -> bool:
    """Every member, including the leading CUT, needs its complete native chain."""
    return any(
        len(chain) > 1 and any(member.id == scene.id for member in chain)
        for chain in continuity_chains(all_scenes)
    )


def require_complete_continuity_selection(
    provided_scene_ids: list[str], all_scenes: list[Scene]
) -> None:
    """Explicit selections authorize whole canonical chains, never implicit expansion."""
    provided = set(provided_scene_ids)
    for chain in continuity_chains(all_scenes):
        required = [scene.id for scene in chain]
        if provided.intersection(required) and not provided.issuperset(required):
            raise AppError(
                "DIRECTOR_CONTINUITY_CHAIN_REQUIRED",
                "Select the complete native continuity chain for Generate All",
                422,
                {"required_scene_ids": required, "provided_scene_ids": provided_scene_ids},
            )
