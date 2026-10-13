from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.deps import AuthPrincipal, expected_revision, require_csrf, require_editor
from apps.api.app.api.ownership import (
    get_owned,
    get_video_output_asset,
    get_video_reference_asset,
    is_admin,
    owner_scope,
    principal_id,
)
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import GenerationAsset, Scene, SceneGeneration, Video
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import Reorder, SceneCreate, SceneDTO, ScenePatch, Selection
from apps.api.app.services.deletion_state import is_logically_deleted
from apps.api.app.services.generation_freshness import (
    is_generation_fresh,
    is_selected_generation_fresh,
)
from apps.api.app.services.video_dependency_guard import lock_video_dependencies

router = APIRouter(tags=["scenes"])


async def _scene_dto(session: AsyncSession, scene: Scene, video: Video) -> SceneDTO:
    return SceneDTO.model_validate(scene).model_copy(
        update={
            "selected_generation_fresh": await is_selected_generation_fresh(session, scene, video),
        }
    )


async def _scene(session: AsyncSession, scene_id: str, user: AuthPrincipal) -> Scene:
    return await get_owned(
        session,
        Scene,
        scene_id,
        user,
        error_code="SCENE_NOT_FOUND",
        message="Scene not found",
    )


async def _video(
    session: AsyncSession,
    video_id: str,
    user: AuthPrincipal,
    *,
    lock: bool = False,
) -> Video:
    if lock:
        video = await session.scalar(
            select(Video).where(Video.id == video_id, owner_scope(Video, user)).with_for_update()
        )
        if video is None:
            raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
        dependencies = await lock_video_dependencies(
            session,
            video,
            user_id=str(principal_id(user)),
            is_admin=is_admin(user),
        )
        return dependencies.video or video
    return await get_owned(
        session,
        Video,
        video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video not found",
    )


async def _require_owned_scene_assets(
    session: AsyncSession, generation_config, user, video: Video
) -> None:
    if generation_config is None:
        return
    values = generation_config.model_dump(mode="python")
    asset_ids = [
        values.get(field)
        for field in (
            "first_frame_asset_id",
            "last_frame_asset_id",
            "source_video_asset_id",
        )
    ]
    for field in (
        "reference_image_asset_ids",
        "reference_video_asset_ids",
        "reference_audio_asset_ids",
    ):
        asset_ids.extend(values.get(field) or [])
    for asset_id in dict.fromkeys(value for value in asset_ids if value is not None):
        await get_video_reference_asset(session, video, str(asset_id), user)


async def _require_owned_generation_assets(
    session: AsyncSession, generation: SceneGeneration, video: Video, user: AuthPrincipal
) -> None:
    asset_links = list(
        (
            await session.scalars(
                select(GenerationAsset).where(GenerationAsset.generation_id == generation.id)
            )
        ).all()
    )
    for link in asset_links:
        if link.role.startswith("DIRECTOR_"):
            await get_video_output_asset(
                session,
                video,
                link.asset_id,
                user,
                role="GENERATED_VIDEO",
                generation_id=generation.id,
            )
            continue
        if link.role not in {
            "FIRST_FRAME",
            "LAST_FRAME",
            "SOURCE_VIDEO",
            "REFERENCE_IMAGE",
            "REFERENCE_AUDIO",
            "REFERENCE_VIDEO",
        }:
            raise AppError("ASSET_NOT_FOUND", "Asset was not found", 404)
        await get_video_reference_asset(session, video, link.asset_id, user)


async def _locked_scene_and_video(
    session: AsyncSession, scene_id: str, user: AuthPrincipal
) -> tuple[Scene, Video]:
    """Lock parent video before child scene to keep every mutation ordered."""
    # Prove visibility before acquiring either row lock. Ownership is inherited
    # from the video and cannot change while the parent lock is held.
    scene = await _scene(session, scene_id, user)
    video = await lock_revisioned_row(session, Video, scene.video_id)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    dependencies = await lock_video_dependencies(
        session,
        video,
        user_id=str(principal_id(user)),
        is_admin=is_admin(user),
    )
    video = dependencies.video or video
    locked_scene = await lock_revisioned_row(
        session, Scene, scene_id, immutable_fields=("video_id",)
    )
    if locked_scene is None:
        raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
    if await is_logically_deleted(session, "SCENE", locked_scene.id, video.id):
        raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
    return locked_scene, video


def _dirty(video: Video) -> None:
    video.revision += 1
    if video.current_final_video_id or video.status == "READY":
        video.status = "DIRTY"
    elif video.status not in {"GENERATING", "FAILED"}:
        video.status = "STORYBOARD_READY" if video.kind == "LONG_VIDEO" else "DRAFT"


@router.get("/videos/{video_id}/scenes", response_model=list[SceneDTO])
async def list_scenes(
    video_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[SceneDTO]:
    video = await _video(session, video_id, user)
    rows = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video_id, owner_scope(Scene, user))
                .order_by(Scene.scene_order, Scene.id)
            )
        ).all()
    )
    return [await _scene_dto(session, row, video) for row in rows]


@router.get("/scenes/{scene_id}", response_model=SceneDTO)
async def get_scene(
    scene_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    scene = await _scene(session, scene_id, user)
    video = await _video(session, scene.video_id, user)
    return await _scene_dto(session, scene, video)


@router.post("/videos/{video_id}/scenes", response_model=SceneDTO, status_code=201)
async def create_scene(
    video_id: str,
    payload: SceneCreate,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    video = await _video(session, video_id, user)
    await _require_owned_scene_assets(session, payload.generation_config, user, video)
    video = await _video(session, video_id, user, lock=True)
    await _require_owned_scene_assets(session, payload.generation_config, user, video)
    if video.kind != "LONG_VIDEO":
        raise AppError("VIDEO_TYPE_CONFLICT", "Quick clips have exactly one scene", 409)
    if video.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Video changed since it was loaded",
            412,
            {"expected": revision, "actual": video.revision},
        )
    await session.scalars(
        select(Scene.id)
        .where(Scene.video_id == video_id, owner_scope(Scene, user))
        .with_for_update()
    )
    scene_count = await session.scalar(
        select(func.count())
        .select_from(Scene)
        .where(Scene.video_id == video_id, owner_scope(Scene, user))
    )
    if scene_count >= 15:
        raise AppError("SCENE_LIMIT_REACHED", "A video can contain at most 15 scenes", 409)
    highest_order = await session.scalar(
        select(func.max(Scene.scene_order)).where(
            Scene.video_id == video_id, owner_scope(Scene, user)
        )
    )
    scene_order = (highest_order if highest_order is not None else -1) + 1
    scene = Scene(
        video_id=video_id,
        scene_order=scene_order,
        **payload.model_dump(mode="json", exclude={"scene_order", "generation_config"}),
        generation_config=payload.generation_config.model_dump(mode="json", exclude_unset=True),
    )
    session.add(scene)
    video.revision += 1
    video.status = "DIRTY" if video.current_final_video_id else "STORYBOARD_READY"
    await session.flush()
    return await _scene_dto(session, scene, video)


@router.patch("/scenes/{scene_id}", response_model=SceneDTO)
async def patch_scene(
    scene_id: str,
    payload: ScenePatch,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    scene = await _scene(session, scene_id, user)
    video = await _video(session, scene.video_id, user)
    await _require_owned_scene_assets(session, payload.generation_config, user, video)
    scene, video = await _locked_scene_and_video(session, scene_id, user)
    await _require_owned_scene_assets(session, payload.generation_config, user, video)
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
    return await _scene_dto(session, scene, video)


@router.post("/videos/{video_id}/scenes/reorder", response_model=list[SceneDTO])
async def reorder(
    video_id: str,
    payload: Reorder,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> list[SceneDTO]:
    await _video(session, video_id, user)
    video = await _video(session, video_id, user, lock=True)
    if video.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Video changed since it was loaded",
            412,
            {"expected": revision, "actual": video.revision},
        )
    scenes = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video_id, owner_scope(Scene, user))
                .with_for_update()
            )
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
    return [await _scene_dto(session, by_id[scene_id], video) for scene_id in provided]


@router.post("/scenes/{scene_id}/select-generation", response_model=SceneDTO)
async def select_generation(
    scene_id: str,
    payload: Selection,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    scene = await _scene(session, scene_id, user)
    generation = await get_owned(
        session,
        SceneGeneration,
        str(payload.generation_id),
        user,
        error_code="GENERATION_NOT_FOUND",
        message="Generation does not belong to this scene",
    )
    if generation.scene_id != scene.id:
        raise AppError("GENERATION_NOT_FOUND", "Generation does not belong to this scene", 404)
    video = await _video(session, scene.video_id, user)
    if generation.video_id != video.id:
        raise AppError("GENERATION_NOT_FOUND", "Generation does not belong to this scene", 404)
    if generation.output_asset_id:
        await get_video_output_asset(
            session,
            video,
            generation.output_asset_id,
            user,
            role="GENERATED_VIDEO",
            generation_id=generation.id,
        )
    await _require_owned_generation_assets(session, generation, video, user)
    scene, video = await _locked_scene_and_video(session, scene_id, user)
    if scene.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Scene changed since it was loaded",
            412,
            {"expected": revision, "actual": scene.revision},
        )
    generation = await session.scalar(
        select(SceneGeneration)
        .where(
            SceneGeneration.id == str(payload.generation_id),
            SceneGeneration.scene_id == scene.id,
            owner_scope(SceneGeneration, user),
        )
        .with_for_update()
    )
    if generation is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation does not belong to this scene", 404)
    await _require_owned_generation_assets(session, generation, video, user)
    if generation.status != "COMPLETED" or not generation.output_asset_id:
        raise AppError("GENERATION_NOT_READY", "Only completed generations can be selected", 409)
    if not await is_generation_fresh(session, scene, video, generation):
        raise AppError(
            "SCENE_SELECTION_STALE",
            "Generation no longer matches current scene inputs",
            409,
            {"scene_id": scene.id, "generation_id": generation.id},
        )
    asset = await get_video_output_asset(
        session,
        video,
        generation.output_asset_id,
        user,
        role="GENERATED_VIDEO",
        generation_id=generation.id,
        for_update=True,
    )
    if asset.status != "READY" or asset.deleted_at is not None:
        raise AppError("GENERATION_OUTPUT_NOT_READY", "Generation output is not ready", 409)
    scene.selected_generation_id = generation.id
    scene.revision += 1
    _dirty(video)
    return await _scene_dto(session, scene, video)


async def _set_enabled(
    session: AsyncSession,
    scene_id: str,
    revision: int,
    enabled: bool,
    user: AuthPrincipal,
) -> SceneDTO:
    scene, video = await _locked_scene_and_video(session, scene_id, user)
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
    return await _scene_dto(session, scene, video)


@router.post("/scenes/{scene_id}/disable", response_model=SceneDTO)
async def disable_scene(
    scene_id: str,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    return await _set_enabled(session, scene_id, revision, False, user)


@router.post("/scenes/{scene_id}/enable", response_model=SceneDTO)
async def enable_scene(
    scene_id: str,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> SceneDTO:
    return await _set_enabled(session, scene_id, revision, True, user)
