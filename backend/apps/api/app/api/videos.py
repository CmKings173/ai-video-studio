from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import apply_patch, pagination
from apps.api.app.api.deps import (
    AuthPrincipal,
    expected_revision,
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.api.ownership import (
    get_owned,
    get_video_reference_asset,
    is_admin,
    owner_scope,
    principal_id,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Scene, SceneGeneration, Video
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import (
    Page,
    SceneDTO,
    StoryboardPublish,
    VideoCreate,
    VideoDetail,
    VideoDTO,
    VideoPatch,
)
from apps.api.app.services.generation_freshness import is_selected_generation_fresh
from apps.api.app.services.idempotency import claim, complete
from apps.api.app.services.storyboard_service import StoryboardService
from apps.api.app.services.video_dependency_guard import (
    lock_creation_dependencies,
    lock_video_dependencies,
    read_creation_dependencies,
    read_video_dependencies,
)

router = APIRouter(prefix="/videos", tags=["videos"])


async def _video(
    session: AsyncSession,
    video_id: str,
    user: AuthPrincipal,
    lock: bool = False,
) -> Video:
    with session.no_autoflush:
        video = await get_owned(
            session,
            Video,
            video_id,
            user,
            error_code="VIDEO_NOT_FOUND",
            message="Video not found",
        )
    dependencies = await read_video_dependencies(
        session,
        video,
        user_id=principal_id(user),
        is_admin=is_admin(user),
        require_active=lock,
    )
    if lock:
        dependencies = await lock_video_dependencies(
            session,
            video,
            user_id=principal_id(user),
            is_admin=is_admin(user),
        )
    return dependencies.video or video


async def _require_owned_scene_assets(
    session: AsyncSession, generation_config, user, video: Video
) -> None:
    if generation_config is None:
        return
    values = (
        generation_config
        if isinstance(generation_config, dict)
        else generation_config.model_dump(mode="python")
    )
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


async def _storyboard_replay(
    session: AsyncSession,
    video: Video,
    replay: dict,
    user: AuthPrincipal,
    expected_scene_count: int,
) -> VideoDetail:
    """Use the cache only to locate the original scenes, never as response data.

    Mutable scene state may advance after publish. Read column snapshots rather
    than refreshing ORM instances, preserving pending edits without autoflush.
    The caller already holds the authoritative Video and parent locks.
    """
    missing = AppError(
        "IDEMPOTENCY_RESOURCE_MISSING", "The original storyboard result is unavailable", 409
    )
    if replay.get("id") != video.id or replay.get("project_id") != video.project_id:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    cached_scenes = replay.get("scenes")
    if not isinstance(cached_scenes, list) or len(cached_scenes) != expected_scene_count:
        raise missing
    identities = []
    for cached in cached_scenes:
        if not isinstance(cached, dict) or not isinstance(cached.get("id"), str):
            raise missing
        if cached.get("video_id") != video.id:
            raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
        identities.append(cached["id"])
    if len(set(identities)) != len(identities):
        raise missing
    with session.no_autoflush:
        rows = (
            (
                await session.execute(
                    select(*Scene.__table__.c)
                    .where(Scene.id.in_(identities))
                    .order_by(Scene.scene_order, Scene.id)
                )
            )
            .mappings()
            .all()
        )
        if any(row["video_id"] != video.id for row in rows):
            raise AppError("SCENE_NOT_FOUND", "Scene not found", 404)
        if len(rows) != len(identities):
            raise missing
        scenes = []
        for row in rows:
            # A transient snapshot cannot mutate the session's identity map.
            scene = Scene(**row)
            await _require_owned_scene_assets(session, scene.generation_config, user, video)
            if scene.selected_generation_id:
                generation = (
                    await session.execute(
                        select(SceneGeneration.scene_id, SceneGeneration.video_id).where(
                            SceneGeneration.id == scene.selected_generation_id,
                            owner_scope(SceneGeneration, user),
                        )
                    )
                ).one_or_none()
                if generation is None:
                    raise missing
                if generation.scene_id != scene.id or generation.video_id != video.id:
                    raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
            scenes.append(
                SceneDTO.model_validate(scene).model_copy(
                    update={
                        "selected_generation_fresh": await is_selected_generation_fresh(
                            session, scene, video
                        )
                    }
                )
            )
        return VideoDetail(**VideoDTO.model_validate(video).model_dump(), scenes=scenes)


@router.get("", response_model=Page[VideoDTO])
async def list_videos(
    paging: tuple[int, int] = Depends(pagination),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    project_id: str | None = Query(default=None, min_length=1, max_length=36),
    product_id: str | None = Query(default=None, min_length=1, max_length=36),
    status: str | None = Query(default=None, min_length=1, max_length=24),
    kind: str | None = Query(default=None, min_length=1, max_length=16),
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[VideoDTO]:
    page, size = paging
    filters = [owner_scope(Video, user)]
    if search:
        filters.append(Video.title.ilike(f"%{search}%"))
    if project_id:
        filters.append(Video.project_id == project_id)
    if product_id:
        filters.append(Video.product_id == product_id)
    if status:
        filters.append(Video.status == status)
    if kind:
        filters.append(Video.kind == kind)
    total_query = select(func.count()).select_from(Video)
    query = select(Video)
    if filters:
        total_query = total_query.where(*filters)
        query = query.where(*filters)
    total = await session.scalar(total_query) or 0
    rows = list(
        (
            await session.scalars(
                query.order_by(Video.created_at.desc(), Video.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[VideoDTO.model_validate(row) for row in rows], total=total, page=page, page_size=size
    )


@router.post("", response_model=VideoDetail, status_code=201)
async def create_video(
    payload: VideoCreate,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> VideoDetail:
    dependency_args = {
        "project_id": str(payload.project_id),
        "product_id": str(payload.product_id) if payload.product_id else None,
        "brand_id": str(payload.brand_id) if payload.brand_id else None,
        "user_id": principal_id(user),
        "is_admin": is_admin(user),
    }
    await read_creation_dependencies(session, **dependency_args)
    dependencies = await lock_creation_dependencies(session, **dependency_args)
    video = Video(
        **payload.model_dump(mode="json"),
        created_by=dependencies.business_owner_id,
        created_actor_id=principal_id(user),
    )
    session.add(video)
    await session.flush()
    scenes: list[Scene] = []
    if video.kind == "QUICK_CLIP":
        scene = Scene(
            video_id=video.id,
            scene_order=0,
            prompt=video.brief,
            duration_seconds=video.target_duration,
            spec={"title": video.title, "purpose": "PRODUCT_DETAIL", "continuity": "CUT"},
        )
        session.add(scene)
        await session.flush()
        scenes.append(scene)
    return VideoDetail(
        **VideoDTO.model_validate(video).model_dump(),
        scenes=[
            SceneDTO.model_validate(scene).model_copy(
                update={
                    "selected_generation_fresh": await is_selected_generation_fresh(
                        session, scene, video
                    ),
                }
            )
            for scene in scenes
        ],
    )


@router.get("/{video_id}", response_model=VideoDetail)
async def get_video(
    video_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> VideoDetail:
    video = await _video(session, video_id, user)
    scenes = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video.id, owner_scope(Scene, user))
                .order_by(Scene.scene_order, Scene.id)
            )
        ).all()
    )
    return VideoDetail(
        **VideoDTO.model_validate(video).model_dump(),
        scenes=[
            SceneDTO.model_validate(scene).model_copy(
                update={
                    "selected_generation_fresh": await is_selected_generation_fresh(
                        session, scene, video
                    ),
                }
            )
            for scene in scenes
        ],
    )


@router.patch("/{video_id}", response_model=VideoDTO)
async def patch_video(
    video_id: str,
    payload: VideoPatch,
    revision: int = Depends(expected_revision),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> VideoDTO:
    video = await _video(session, video_id, user, True)
    changed = payload.model_fields_set
    apply_patch(video, payload, revision)
    if video.kind == "QUICK_CLIP" and "brief" in changed:
        scene = await session.scalar(
            select(Scene)
            .where(Scene.video_id == video.id, owner_scope(Scene, user))
            .order_by(Scene.scene_order)
            .with_for_update()
            .limit(1)
        )
        if scene:
            scene.prompt = video.brief
            scene.revision += 1
    if video.current_final_video_id:
        video.status = "DIRTY"
    elif changed & {"brief", "config"}:
        video.status = "DRAFT" if video.kind == "QUICK_CLIP" else "STORYBOARD_READY"
    return VideoDTO.model_validate(video)


@router.post("/{video_id}/storyboard/preview")
async def storyboard_preview(
    video_id: str,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    video = await _video(session, video_id, user)
    if video.kind != "LONG_VIDEO":
        raise AppError("VIDEO_TYPE_CONFLICT", "Storyboard is only available for long videos", 409)
    dependencies = await read_video_dependencies(
        session,
        video,
        user_id=principal_id(user),
        is_admin=is_admin(user),
    )
    product = dependencies.product
    brand = dependencies.effective_brand
    context = {
        "brief": video.brief,
        "product": (
            {
                "name": product.name,
                "description": product.description,
                "context": product.context,
            }
            if product
            else {}
        ),
        "brand": (
            {
                "name": brand.name,
                "description": brand.description,
                "context": brand.context,
            }
            if brand
            else {}
        ),
        "planner": video.config.get("storyboard_planner", {}),
    }
    total_seconds = int(video.target_duration)
    video_revision = video.revision
    await session.rollback()
    return await StoryboardService(settings).plan(
        context,
        total_seconds,
        video_revision=video_revision,
    )


@router.post("/{video_id}/storyboard/publish", response_model=VideoDetail)
async def storyboard_publish(
    video_id: str,
    payload: StoryboardPublish,
    revision: int = Depends(expected_revision),
    key: str = Depends(idempotency_key),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> VideoDetail:
    # Authorize before claiming idempotency or staging any storyboard changes.
    video = await _video(session, video_id, user, True)
    for scene in payload.scenes:
        await _require_owned_scene_assets(session, scene.generation_config, user, video)
    idempotency, replay = await claim(
        session,
        user_id=principal_id(user),
        operation=f"storyboard-publish:{video_id}",
        key=key,
        payload={"revision": revision, **payload.model_dump(mode="json")},
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return await _storyboard_replay(session, video, replay, user, len(payload.scenes))
    if video.kind != "LONG_VIDEO":
        raise AppError("VIDEO_TYPE_CONFLICT", "Storyboard is only available for long videos", 409)
    if video.revision != revision:
        raise AppError(
            "REVISION_CONFLICT",
            "Video changed since preview",
            412,
            {"expected": revision, "actual": video.revision},
        )
    existing = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video.id, owner_scope(Scene, user))
                .order_by(Scene.scene_order)
            )
        ).all()
    )
    if existing and not payload.replace:
        raise AppError(
            "STORYBOARD_EXISTS", "Set replace=true to replace an untouched storyboard", 409
        )
    if existing:
        has_history = await session.scalar(
            select(SceneGeneration.id)
            .where(SceneGeneration.video_id == video.id, owner_scope(SceneGeneration, user))
            .limit(1)
        )
        if has_history:
            raise AppError(
                "STORYBOARD_HAS_HISTORY",
                "Storyboard with generation history cannot be replaced",
                409,
            )
        expected_scenes = {
            str(scene_id): scene_revision
            for scene_id, scene_revision in (payload.replace_scene_revisions or {}).items()
        }
        actual_scenes = {scene.id: scene.revision for scene in existing}
        if expected_scenes != actual_scenes:
            raise AppError(
                "STORYBOARD_REPLACE_CONFLICT",
                "Replace requires the exact current scene revisions",
                412,
                {"expected": expected_scenes, "actual": actual_scenes},
            )
    total = sum(scene.duration_seconds for scene in payload.scenes)
    if abs(total - video.target_duration) > 0.001:
        raise AppError(
            "STORYBOARD_DURATION_INVALID",
            "Scene durations must equal video target duration",
            422,
            {"expected": video.target_duration, "actual": total},
        )
    for index, value in enumerate(payload.scenes):
        if value.scene_order is not None and value.scene_order != index:
            raise AppError(
                "STORYBOARD_ORDER_INVALID",
                "Scene order must be contiguous from zero",
                422,
            )
    # Validate the complete replacement before deleting or staging any scenes.
    if existing:
        for scene in existing:
            await session.delete(scene)
        await session.flush()
    scenes = []
    for index, value in enumerate(payload.scenes):
        scene = Scene(
            video_id=video.id,
            scene_order=index,
            **value.model_dump(mode="json", exclude={"scene_order"}),
        )
        session.add(scene)
        scenes.append(scene)
    video.status = "STORYBOARD_READY"
    video.revision += 1
    await session.flush()
    response = VideoDetail(
        **VideoDTO.model_validate(video).model_dump(),
        scenes=[
            SceneDTO.model_validate(scene).model_copy(
                update={
                    "selected_generation_fresh": await is_selected_generation_fresh(
                        session, scene, video
                    ),
                }
            )
            for scene in scenes
        ],
    )
    complete(idempotency, response.model_dump(mode="json"), 200)
    return response
