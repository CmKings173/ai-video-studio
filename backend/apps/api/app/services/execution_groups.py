"""Plan ordered execution boundaries without writing any jobs or invoking providers."""

from dataclasses import dataclass

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene
from apps.api.app.schemas.api import GenerationRequest
from apps.api.app.services.continuity_groups import (
    continuity_chains,
    require_complete_continuity_selection,
)


@dataclass
class ExecutionGroup:
    scenes: list[Scene]
    execution_scope: str


def plan_execution_groups(
    scenes: list[Scene], all_scenes: list[Scene], effective: list[GenerationRequest]
) -> list[ExecutionGroup]:
    requests = {scene.id: request for scene, request in zip(scenes, effective, strict=True)}
    selected = set(requests)
    chains = continuity_chains(all_scenes)
    enabled_ids = {scene.id for chain in chains for scene in chain}
    if not selected.issubset(enabled_ids):
        raise AppError("SCENE_SELECTION_INVALID", "Every selected scene must be enabled", 422)
    predecessors = {
        member.id: chain[index - 1]
        for chain in chains
        for index, member in enumerate(chain)
        if index > 0
    }
    groups: list[ExecutionGroup] = []
    for scene in sorted(scenes, key=lambda row: (row.scene_order, row.id)):
        continuity = (scene.spec or {}).get("continuity", "CUT")
        if continuity not in {"CUT", "CONTINUOUS"}:
            raise AppError("DIRECTOR_GROUP_INVALID", "Unknown scene continuity", 422)
        predecessor = predecessors.get(scene.id)
        if predecessor is not None:
            if predecessor.id not in selected:
                raise AppError(
                    "DIRECTOR_CONTINUITY_PREDECESSOR_REQUIRED",
                    "A continuous scene requires its immediate predecessor in the batch",
                    422,
                )
            if not groups or groups[-1].scenes[-1].id != predecessor.id:
                raise AppError("DIRECTOR_GROUP_INVALID", "Noncontiguous continuity chain", 422)
            groups[-1].scenes.append(scene)
            groups[-1].execution_scope = "aggregate"
        else:
            request = requests[scene.id]
            scope = (
                "aggregate"
                if request.motion_context and request.motion_context.enabled
                else "single_scene"
            )
            groups.append(ExecutionGroup([scene], scope))
    require_complete_continuity_selection([scene.id for scene in scenes], all_scenes)
    return groups
