from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import apply_patch, pagination
from apps.api.app.api.deps import (
    expected_revision,
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Brand, Product, Project, Scene, SceneGeneration, User, Video
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
from apps.api.app.services.idempotency import claim, complete
from apps.api.app.services.storyboard_service import StoryboardService

router = APIRouter(prefix="/videos", tags=["videos"])


async def _video(session: AsyncSession, video_id: str, lock: bool = False) -> Video:
    video = await session.get(Video, video_id, with_for_update=lock)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video not found", 404)
    return video


@router.get("", response_model=Page[VideoDTO])
async def list_videos(
    paging: tuple[int, int] = Depends(pagination),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    project_id: str | None = Query(default=None, min_length=1, max_length=36),
    product_id: str | None = Query(default=None, min_length=1, max_length=36),
    status: str | None = Query(default=None, min_length=1, max_length=24),
    kind: str | None = Query(default=None, min_length=1, max_length=16),
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[VideoDTO]:
    page, size = paging
    filters = []
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
                query
                .order_by(Video.created_at.desc(), Video.id.desc())
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
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> VideoDetail:
    project = await session.get(Project, str(payload.project_id))
    if project is None or project.archived:
        raise AppError("PROJECT_NOT_ACTIVE", "Project is missing or archived", 409)
    if payload.product_id:
        product = await session.get(Product, str(payload.product_id))
        if product is None or product.archived:
            raise AppError("PRODUCT_NOT_ACTIVE", "Product is missing or archived", 409)
        if payload.brand_id and product.brand_id and str(payload.brand_id) != product.brand_id:
            raise AppError(
                "PRODUCT_BRAND_CONFLICT",
                "Selected product belongs to a different brand",
                422,
            )
    if payload.brand_id:
        brand = await session.get(Brand, str(payload.brand_id))
        if brand is None or brand.archived:
            raise AppError("BRAND_NOT_ACTIVE", "Brand is missing or archived", 409)
    video = Video(**payload.model_dump(mode="json"), created_by=user.id)
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
        scenes=[SceneDTO.model_validate(scene) for scene in scenes],
    )


@router.get("/{video_id}", response_model=VideoDetail)
async def get_video(
    video_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> VideoDetail:
    video = await _video(session, video_id)
    scenes = list(
        (
            await session.scalars(
                select(Scene)
                .where(Scene.video_id == video.id)
                .order_by(Scene.scene_order, Scene.id)
            )
        ).all()
    )
    return VideoDetail(
        **VideoDTO.model_validate(video).model_dump(),
        scenes=[SceneDTO.model_validate(scene) for scene in scenes],
    )


@router.patch("/{video_id}", response_model=VideoDTO)
async def patch_video(
    video_id: str,
    payload: VideoPatch,
    revision: int = Depends(expected_revision),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> VideoDTO:
    video = await _video(session, video_id, True)
    changed = payload.model_fields_set
    apply_patch(video, payload, revision)
    if video.kind == "QUICK_CLIP" and "brief" in changed:
        scene = await session.scalar(
            select(Scene)
            .where(Scene.video_id == video.id)
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
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict:
    video = await _video(session, video_id)
    if video.kind != "LONG_VIDEO":
        raise AppError("VIDEO_TYPE_CONFLICT", "Storyboard is only available for long videos", 409)
    product = await session.get(Product, video.product_id) if video.product_id else None
    brand_id = video.brand_id or (product.brand_id if product else None)
    brand = await session.get(Brand, brand_id) if brand_id else None
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
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> VideoDetail:
    idempotency, replay = await claim(
        session,
        user_id=user.id,
        operation=f"storyboard-publish:{video_id}",
        key=key,
        payload={"revision": revision, **payload.model_dump(mode="json")},
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return VideoDetail.model_validate(replay)
    video = await _video(session, video_id, True)
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
                select(Scene).where(Scene.video_id == video.id).order_by(Scene.scene_order)
            )
        ).all()
    )
    if existing and not payload.replace:
        raise AppError(
            "STORYBOARD_EXISTS", "Set replace=true to replace an untouched storyboard", 409
        )
    if existing:
        has_history = await session.scalar(
            select(SceneGeneration.id).where(SceneGeneration.video_id == video.id).limit(1)
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
        for scene in existing:
            await session.delete(scene)
        await session.flush()
    total = sum(scene.duration_seconds for scene in payload.scenes)
    if abs(total - video.target_duration) > 0.001:
        raise AppError(
            "STORYBOARD_DURATION_INVALID",
            "Scene durations must equal video target duration",
            422,
            {"expected": video.target_duration, "actual": total},
        )
    scenes = []
    for index, value in enumerate(payload.scenes):
        if value.scene_order is not None and value.scene_order != index:
            raise AppError(
                "STORYBOARD_ORDER_INVALID",
                "Scene order must be contiguous from zero",
                422,
            )
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
        scenes=[SceneDTO.model_validate(scene) for scene in scenes],
    )
    complete(idempotency, response.model_dump(mode="json"), 200)
    return response
