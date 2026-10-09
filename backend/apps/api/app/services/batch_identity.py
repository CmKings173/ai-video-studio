"""Batch editor state and the one revision transition made by automatic selection."""

from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.db.models import Scene, SceneGeneration, Video
from apps.api.app.services.generation_freshness import generation_dependencies


async def capture_batch_inputs(
    session: AsyncSession,
    video: Video,
    scenes: list[Scene],
) -> dict[str, Any]:
    """Include external semantic prompt dependencies in batch replay identity."""
    return {
        **batch_inputs(video, scenes),
        "generation_dependencies": await generation_dependencies(session, video),
    }


def batch_inputs(video: Video, scenes: list[Scene]) -> dict[str, Any]:
    return {
        "video_revision": video.revision,
        "video": {
            name: getattr(video, name)
            for name in (
                "title",
                "brief",
                "config",
                "kind",
                "aspect_ratio",
                "target_duration",
                "project_id",
                "product_id",
                "brand_id",
            )
        },
        "scenes": [
            {
                "id": scene.id,
                "revision": scene.revision,
                "selected_generation_id": scene.selected_generation_id,
                "scene_order": scene.scene_order,
                "enabled": scene.enabled,
                "prompt": scene.prompt,
                "negative_prompt": scene.negative_prompt,
                "duration_seconds": scene.duration_seconds,
                "spec": scene.spec,
                "generation_config": getattr(scene, "generation_config", None),
            }
            for scene in scenes
        ],
    }


async def unchanged_batch_inputs(
    session: AsyncSession, saved: dict[str, Any], current: dict[str, Any]
) -> bool:
    """Discount only a proven Dispatcher._finish auto-selection, never an editor edit."""
    normalized = deepcopy(current)
    before = {scene["id"]: scene for scene in saved["scenes"]}
    automatic_selections = 0
    for scene in normalized["scenes"]:
        original = before.get(scene["id"])
        if original is None:
            return False
        # Before the config column existed, the same empty configuration was recorded as None.
        if original.get("generation_config") is None and scene.get("generation_config") == {}:
            scene["generation_config"] = None
        selected = scene["selected_generation_id"]
        if selected == original["selected_generation_id"]:
            continue
        if (
            original["selected_generation_id"] is not None
            or not selected
            or not scene["enabled"]
            or scene["revision"] != original["revision"] + 1
        ):
            return False
        generation = await session.get(SceneGeneration, selected)
        if (
            generation is None
            or generation.scene_id != scene["id"]
            or generation.status != "COMPLETED"
            or generation.operation != "ORIGINAL"
            or generation.input_snapshot.get("scene_revision") != original["revision"]
        ):
            return False
        scene["selected_generation_id"] = None
        scene["revision"] = original["revision"]
        automatic_selections += 1
    normalized["video_revision"] -= automatic_selections
    return normalized == saved
