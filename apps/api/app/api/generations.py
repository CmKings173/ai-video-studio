from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import pagination
from apps.api.app.api.deps import (
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Brand,
    GenerationAttempt,
    Product,
    Scene,
    SceneGeneration,
    User,
    Video,
    utcnow,
)
from apps.api.app.db.session import get_session
from apps.api.app.schemas.api import (
    BatchGenerationDTO,
    GenerateAll,
    GenerationAttemptDTO,
    GenerationDTO,
    GenerationRequest,
    Page,
    PromptPreviewDTO,
    VariationRequest,
)
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.idempotency import claim, complete
from apps.api.app.services.prompt_engine import PromptEngine
from workers.common import GENERATION_TERMINAL, refresh_video

router = APIRouter(tags=["generations"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post("/scenes/{scene_id}/prompt-preview", response_model=PromptPreviewDTO)
async def preview_prompt(
    scene_id: str,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PromptPreviewDTO:
    scene = await session.get(Scene, scene_id)
    if scene is None:
        raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
    video = await session.get(Video, scene.video_id)
    product = await session.get(Product, video.product_id) if video.product_id else None
    brand_id = video.brand_id or (product.brand_id if product else None)
    brand = await session.get(Brand, brand_id) if brand_id else None
    context = {
        "brief": video.brief,
        "scene": {
            "prompt": scene.prompt,
            "negative_prompt": scene.negative_prompt,
            "spec": scene.spec,
        },
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
        "music": video.config.get("music", ""),
    }
    enhancer = video.config.get("prompt_enhancer")
    scene_revision, video_revision = scene.revision, video.revision
    await session.rollback()
    result = await PromptEngine(settings).compose(context, enhancer)
    return PromptPreviewDTO(
        raw_prompt=result.raw_prompt,
        execution_prompt=result.execution_prompt,
        enhanced=result.enhanced,
        warnings=list(result.warnings),
        scene_revision=scene_revision,
        video_revision=video_revision,
    )


async def _create(
    *,
    scene_id: str,
    payload: GenerationRequest,
    request: Request,
    key: str,
    user: User,
    session: AsyncSession,
    settings: Settings,
    operation_name: str,
) -> GenerationDTO:
    record, replay = await claim(
        session,
        user_id=user.id,
        operation=f"{operation_name}:{scene_id}",
        key=key,
        payload=payload.model_dump(mode="json"),
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return GenerationDTO.model_validate(replay)
    generation = await GenerationService(settings).create(
        session,
        scene_id=scene_id,
        request=payload,
        user_id=user.id,
        request_id=_request_id(request),
    )
    await session.flush()
    response = GenerationDTO.model_validate(generation)
    complete(record, response.model_dump(mode="json"), 202)
    return response


@router.post(
    "/scenes/{scene_id}/generations",
    response_model=GenerationDTO,
    status_code=202,
)
async def create_generation(
    scene_id: str,
    payload: GenerationRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GenerationDTO:
    return await _create(
        scene_id=scene_id,
        payload=payload,
        request=request,
        key=key,
        user=user,
        session=session,
        settings=settings,
        operation_name="create-generation",
    )


@router.post(
    "/scenes/{scene_id}/variations",
    response_model=GenerationDTO,
    status_code=202,
)
async def create_variation(
    scene_id: str,
    payload: VariationRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GenerationDTO:
    values = payload.model_dump()
    values["operation"] = "VARIATION"
    generation_request = GenerationRequest.model_validate(values)
    return await _create(
        scene_id=scene_id,
        payload=generation_request,
        request=request,
        key=key,
        user=user,
        session=session,
        settings=settings,
        operation_name="create-variation",
    )


@router.post(
    "/videos/{video_id}/generate-all",
    response_model=BatchGenerationDTO,
    status_code=202,
)
async def generate_all(
    video_id: str,
    payload: GenerateAll,
    request: Request,
    key: str = Depends(idempotency_key),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BatchGenerationDTO:
    record, replay = await claim(
        session,
        user_id=user.id,
        operation=f"generate-all:{video_id}",
        key=key,
        payload=payload.model_dump(mode="json"),
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return BatchGenerationDTO.model_validate(replay)
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
    query = (
        select(Scene)
        .where(Scene.video_id == video.id, Scene.enabled.is_(True))
        .order_by(Scene.scene_order, Scene.id)
    )
    scenes = list((await session.scalars(query)).all())
    requested_ids = (
        [str(value) for value in payload.scene_ids] if payload.scene_ids is not None else None
    )
    if requested_ids is not None:
        if len(requested_ids) != len(set(requested_ids)):
            raise AppError("SCENE_SELECTION_INVALID", "scene_ids must be unique", 422)
        by_id = {scene.id: scene for scene in scenes}
        if any(scene_id not in by_id for scene_id in requested_ids):
            raise AppError(
                "SCENE_SELECTION_INVALID",
                "Every selected scene must be enabled and belong to the video",
                422,
            )
        scenes = [by_id[scene_id] for scene_id in requested_ids]
    else:
        scenes = [scene for scene in scenes if scene.selected_generation_id is None]
    if not scenes:
        raise AppError("NO_SCENES_TO_GENERATE", "No eligible scenes need generation", 409)

    service = GenerationService(settings)
    generations = []
    for scene in scenes:
        generation = await service.create(
            session,
            scene_id=scene.id,
            request=payload.settings,
            user_id=user.id,
            request_id=_request_id(request),
        )
        generations.append(GenerationDTO.model_validate(generation))
    response = BatchGenerationDTO(video_id=video.id, generations=generations)
    complete(record, response.model_dump(mode="json"), 202)
    return response


@router.get("/scenes/{scene_id}/generations", response_model=Page[GenerationDTO])
async def list_scene_generations(
    scene_id: str,
    paging: tuple[int, int] = Depends(pagination),
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[GenerationDTO]:
    if await session.get(Scene, scene_id) is None:
        raise AppError("SCENE_NOT_FOUND", "Scene was not found", 404)
    page, size = paging
    where = SceneGeneration.scene_id == scene_id
    total = (
        await session.scalar(select(func.count()).select_from(SceneGeneration).where(where)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(SceneGeneration)
                .where(where)
                .order_by(SceneGeneration.generation_no.desc(), SceneGeneration.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[GenerationDTO.model_validate(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.get("/generations/{generation_id}", response_model=GenerationDTO)
async def get_generation(
    generation_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> GenerationDTO:
    generation = await session.get(SceneGeneration, generation_id)
    if generation is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    return GenerationDTO.model_validate(generation)


@router.get(
    "/generations/{generation_id}/attempts",
    response_model=list[GenerationAttemptDTO],
)
async def list_generation_attempts(
    generation_id: str,
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[GenerationAttemptDTO]:
    if await session.get(SceneGeneration, generation_id) is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    rows = list(
        (
            await session.scalars(
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation_id)
                .order_by(GenerationAttempt.attempt_no, GenerationAttempt.id)
            )
        ).all()
    )
    return [GenerationAttemptDTO.model_validate(row) for row in rows]


@router.post("/generations/{generation_id}/cancel", response_model=GenerationDTO)
async def cancel_generation(
    generation_id: str,
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> GenerationDTO:
    generation_ref = await session.get(SceneGeneration, generation_id)
    if generation_ref is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    video = await session.get(Video, generation_ref.video_id, with_for_update=True)
    generation = await session.scalar(
        select(SceneGeneration)
        .where(SceneGeneration.id == generation_id)
        .with_for_update()
    )
    if generation is None or video is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    if generation.status in GENERATION_TERMINAL:
        return GenerationDTO.model_validate(generation)
    generation.revision += 1
    generation.progress_updated_at = utcnow()
    if generation.status == "CREATED":
        generation.status = "CANCELLED"
        generation.phase = "CANCELLED"
        generation.finished_at = utcnow()
        generation.claimed_by = None
        generation.lease_expires_at = None
    else:
        generation.status = "CANCEL_REQUESTED"
        generation.phase = "CANCELLING"
    await refresh_video(session, video.id)
    return GenerationDTO.model_validate(generation)
