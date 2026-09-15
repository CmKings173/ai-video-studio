from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import expected_revision, require_csrf, require_editor
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset, Scene, SceneGeneration, User, Video
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import Reorder, SceneCreate, SceneDTO, ScenePatch, Selection

router = APIRouter(tags=["scenes"])


async def _scene(session: AsyncSession, scene_id: str, lock: bool = False) -> Scene:
    scene = await session.get(Scene, scene_id, with_for_update=lock)
    if scene is None:
        raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
    return scene


async def _locked_scene_and_video(session: AsyncSession, scene_id: str) -> tuple[Scene, Video]:
    """Lock parent video before child scene to keep every mutation ordered."""
    scene_ref = await session.get(Scene, scene_id)
    if scene_ref is None:
        raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
    video = await session.get(Video, scene_ref.video_id, with_for_update=True)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    scene = await session.scalar(select(Scene).where(Scene.id == scene_id).with_for_update())
    if scene is None:
        raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
    return scene, video


def _dirty(video: Video) -> None:
    video.revision += 1
    if video.current_final_video_id or video.status == "READY":
        video.status = "DIRTY"
    elif video.status not in {"GENERATING", "FAILED"}:
        video.status = "STORYBOARD_READY" if video.kind == "LONG_VIDEO" else "DRAFT"


@router.get("/videos/{video_id}/scenes", response_model=list[SceneDTO])
async def list_scenes(
    video_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[SceneDTO]:
    if await session.get(Video, video_id) is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    rows = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video_id)
                .order_by(Scene.scene_order, Scene.id)
            )
        ).all()
    )
    return [SceneDTO.model_validate(row) for row in rows]


@router.get("/scenes/{scene_id}", response_model=SceneDTO)
async def get_scene(
    scene_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    return SceneDTO.model_validate(await _scene(session, scene_id))


@router.post("/videos/{video_id}/scenes", response_model=SceneDTO, status_code=201)
async def create_scene(
    video_id: str,
    payload: SceneCreate,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    if video.kind != "LONG_VIDEO":
        raise AppError("VIDEO_TYPE_CONFLICT", "Quick clips have exactly one scene", 409)
    if video.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Video changed since it was loaded",
            412,
            {"expected": revision, "actual": video.revision},
        )
    await session.scalars(select(Scene.id).where(Scene.video_id == video_id).with_for_update())
    scene_count = await session.scalar(
        select(func.count()).select_from(Scene).where(Scene.video_id == video_id)
    )
    if scene_count >= 15:
        raise AppError("SCENE_LIMIT_REACHED", "A video can contain at most 15 scenes", 409)
    highest_order = await session.scalar(
        select(func.max(Scene.scene_order)).where(Scene.video_id == video_id)
    )
    scene_order = (highest_order if highest_order is not None else -1) + 1
    scene = Scene(
        video_id=video_id,
        scene_order=scene_order,
        **payload.model_dump(mode="json", exclude={"scene_order"}),
    )
    session.add(scene)
    video.revision += 1
    video.status = "DIRTY" if video.current_final_video_id else "STORYBOARD_READY"
    await session.flush()
    return SceneDTO.model_validate(scene)


@router.patch("/scenes/{scene_id}", response_model=SceneDTO)
async def patch_scene(
    scene_id: str,
    payload: ScenePatch,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    scene, video = await _locked_scene_and_video(session, scene_id)
    if scene.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Scene changed since it was loaded",
            412,
            {"expected": revision, "actual": scene.revision},
        )
    for key, value in payload.model_dump(exclude_unset=True, mode="json").items():
        setattr(scene, key, value)
    scene.revision += 1
    _dirty(video)
    return SceneDTO.model_validate(scene)


@router.post("/videos/{video_id}/scenes/reorder", response_model=list[SceneDTO])
async def reorder(
    video_id: str,
    payload: Reorder,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> list[SceneDTO]:
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    if video.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Video changed since it was loaded",
            412,
            {"expected": revision, "actual": video.revision},
        )
    scenes = list(
        (
            await session.scalars(select(Scene).where(Scene.video_id == video_id).with_for_update())
        ).all()
    )
    provided = [str(value) for value in payload.scene_ids]
    if len(provided) != len(set(provided)) or set(provided) != {scene.id for scene in scenes}:
        raise AppError(
            "SCENE_ORDER_INVALID", "scene_ids must contain every scene exactly once", 422
        )
    by_id = {scene.id: scene for scene in scenes}
    temporary_offset = max((scene.scene_order for scene in scenes), default=0) + len(scenes) + 1
    for index, scene_id in enumerate(provided):
        by_id[scene_id].scene_order = temporary_offset + index
    await session.flush()
    for index, scene_id in enumerate(provided):
        by_id[scene_id].scene_order = index
        by_id[scene_id].revision += 1
    video.revision += 1
    if video.current_final_video_id or video.status == "READY":
        video.status = "DIRTY"
    return [SceneDTO.model_validate(by_id[scene_id]) for scene_id in provided]


@router.post("/scenes/{scene_id}/select-generation", response_model=SceneDTO)
async def select_generation(
    scene_id: str,
    payload: Selection,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    scene, video = await _locked_scene_and_video(session, scene_id)
    if scene.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Scene changed since it was loaded",
            412,
            {"expected": revision, "actual": scene.revision},
        )
    generation = await session.scalar(
        select(SceneGeneration)
        .where(SceneGeneration.id == str(payload.generation_id))
        .with_for_update()
    )
    if generation is None or generation.scene_id != scene.id:
        raise AppError("GENERATION_NOT_FOUND", "Generation does not belong to this scene", 404)
    if generation.status != "COMPLETED" or not generation.output_asset_id:
        raise AppError("GENERATION_NOT_READY", "Only completed generations can be selected", 409)
    asset = await session.get(Asset, generation.output_asset_id, with_for_update=True)
    if asset is None or asset.status != "READY":
        raise AppError("GENERATION_OUTPUT_NOT_READY", "Generation output is not ready", 409)
    scene.selected_generation_id = generation.id
    scene.revision += 1
    _dirty(video)
    return SceneDTO.model_validate(scene)


async def _set_enabled(
    session: AsyncSession, scene_id: str, revision: int, enabled: bool
) -> SceneDTO:
    scene, video = await _locked_scene_and_video(session, scene_id)
    if scene.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Scene changed since it was loaded",
            412,
            {"expected": revision, "actual": scene.revision},
        )
    if scene.enabled != enabled:
        scene.enabled = enabled
        scene.revision += 1
        _dirty(video)
    return SceneDTO.model_validate(scene)


@router.post("/scenes/{scene_id}/disable", response_model=SceneDTO)
async def disable_scene(
    scene_id: str,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    return await _set_enabled(session, scene_id, revision, False)


@router.post("/scenes/{scene_id}/enable", response_model=SceneDTO)
async def enable_scene(
    scene_id: str,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    return await _set_enabled(session, scene_id, revision, True)
