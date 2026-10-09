from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
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
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    Brand,
    DirectorRun,
    DirectorRunAttempt,
    DirectorRunMember,
    GenerationAttempt,
    IdempotencyKey,
    Product,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.db.session import get_session
from apps.api.app.providers.minimax_h3_director.capabilities import (
    qualified_capabilities,
    source_capabilities,
)
from apps.api.app.providers.minimax_h3_director.graph_identity import is_director_graph
from apps.api.app.providers.minimax_h3_director.qualification import (
    qualified_audio_modes,
    qualified_features,
    verified_settings,
)
from apps.api.app.schemas.api import (
    BatchGenerationDTO,
    ExecutionGroupDTO,
    GenerateAll,
    GenerationAttemptDTO,
    GenerationCapabilitiesDTO,
    GenerationDTO,
    GenerationRequest,
    Page,
    PromptPreviewDTO,
    PromptPreviewRequest,
    RegenerateRequest,
    VariationRequest,
)
from apps.api.app.services.batch_identity import capture_batch_inputs, unchanged_batch_inputs
from apps.api.app.services.continuity_groups import (
    expand_continuity_selection,
    require_complete_continuity_selection,
)
from apps.api.app.services.director_run_service import (
    create_director_run,
    persist_prepared_director_run,
)
from apps.api.app.services.execution_groups import plan_execution_groups
from apps.api.app.services.generation_freshness import is_selected_generation_fresh
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.idempotency import claim, complete, payload_hash
from apps.api.app.services.prompt_engine import PromptEngine
from apps.api.app.services.workflow_contracts import (
    approved_record,
    asset_slot_capacity,
    native_asset_slots,
    require_contract,
    resolve_canvas,
)
from apps.api.app.services.workflow_router import (
    RoutingInput,
    WorkflowRoutingError,
    director_task_for_mode,
    select_mode,
)
from workers.common import GENERATION_TERMINAL, refresh_video

router = APIRouter(tags=["generations"])


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@router.post("/scenes/{scene_id}/prompt-preview", response_model=PromptPreviewDTO)
async def preview_prompt(
    scene_id: str,
    payload: PromptPreviewRequest | None = Body(default=None),
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
    service = GenerationService(settings)
    supplied = payload if isinstance(payload, PromptPreviewRequest) else GenerationRequest()
    effective = service.effective_request(scene, supplied)
    try:
        mode = select_mode(
            RoutingInput(
                requested_mode=effective.mode,
                first_frame_asset_id=effective.first_frame_asset_id,
                last_frame_asset_id=effective.last_frame_asset_id,
                reference_image_asset_ids=effective.reference_image_asset_ids,
                reference_video_asset_ids=effective.reference_video_asset_ids,
                reference_audio_asset_ids=effective.reference_audio_asset_ids,
                source_video_asset_id=(
                    str(effective.source_video_asset_id)
                    if effective.source_video_asset_id
                    else None
                ),
            )
        )
    except WorkflowRoutingError as exc:
        raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc
    _, approved = await service._workflow(
        session,
        mode,
        effective.workflow_id,
        effective.quality_profile or "STANDARD",
        effective.aspect_ratio or video.aspect_ratio,
    )
    assets, _, _ = await service._load_assets(session, effective, video)
    scene_revision, video_revision = scene.revision, video.revision
    await session.rollback()
    result = await PromptEngine(settings).compose(context, enhancer)
    return PromptPreviewDTO(
        raw_prompt=result.raw_prompt,
        execution_prompt=service.finish_prompt(
            result.execution_prompt, context["scene"]["negative_prompt"], approved, assets
        ),
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
        payload=payload.model_dump(mode="json", exclude_unset=True),
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
    values = payload.model_dump(exclude_unset=True)
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


@router.post("/scenes/{scene_id}/regenerations", response_model=GenerationDTO, status_code=202)
async def create_regeneration(
    scene_id: str,
    payload: RegenerateRequest,
    request: Request,
    key: str = Depends(idempotency_key),
    user: User = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> GenerationDTO:
    values = payload.model_dump(exclude_unset=True)
    values["operation"] = "REGENERATE"
    return await _create(
        scene_id=scene_id,
        payload=GenerationRequest.model_validate(values),
        request=request,
        key=key,
        user=user,
        session=session,
        settings=settings,
        operation_name="create-regeneration",
    )


@router.get("/generation-capabilities", response_model=GenerationCapabilitiesDTO)
async def generation_capabilities(
    user: User = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> GenerationCapabilitiesDTO:
    combinations, disabled = [], []
    records = list(
        (
            await session.scalars(
                select(WorkflowRecord).order_by(
                    WorkflowRecord.mode,
                    WorkflowRecord.quality_profile,
                    WorkflowRecord.code,
                    WorkflowRecord.version,
                )
            )
        ).all()
    )
    for record in records:
        if not is_director_graph(record.workflow) or record.profile.get("retired"):
            continue
        identity = {
            "workflow_id": record.id,
            "mode": record.mode,
            "quality_profile": record.quality_profile,
            "execution_scope": record.execution_scope,
        }
        try:
            if not record.enabled:
                raise AppError("WORKFLOW_NOT_APPROVED", "Workflow is disabled", 409)
            approved = approved_record(record)
            contract = require_contract(record, approved)
            is_director = is_director_graph(approved.workflow)
            for case in record.profile["execution_evidence"]["combinations"]:
                ratio = case["aspect_ratio"]
                width, height = resolve_canvas(contract.resolution, approved, ratio)
                p = GenerationService._profile(record)
                settings_context = {
                    "base_canvas": (width, height),
                    "task": director_task_for_mode(record.mode),
                }
                features = (
                    qualified_features(record.profile, **settings_context) if is_director else {}
                )
                combinations.append(
                    {
                        **identity,
                        "aspect_ratio": ratio,
                        "resolved_width": width,
                        "resolved_height": height,
                        "steps": contract.steps,
                        "fps": contract.fps,
                        "workflow_version": record.version,
                        "workflow_hash": record.workflow_hash,
                        "provider": (
                            "minimax_h3_director"
                        ),
                        "director_task": director_task_for_mode(record.mode),
                        "runnable": True,
                        "execution_scope": record.execution_scope,
                        "required_asset_slots": (
                            [] if is_director else sorted(native_asset_slots(approved))
                        ),
                        "max_reference_images": (
                            min(9, p.max_reference_images)
                            if is_director
                            else asset_slot_capacity(
                                approved, "REFERENCE_IMAGE", min(9, p.max_reference_images)
                            )
                        ),
                        "max_reference_videos": (
                            (0 if record.mode == "rv2v" else min(3, p.max_reference_videos))
                            if is_director
                            else asset_slot_capacity(
                                approved, "REFERENCE_VIDEO", min(3, p.max_reference_videos)
                            )
                        ),
                        "max_reference_audio": (
                            min(3, p.max_reference_audio)
                            if is_director
                            else asset_slot_capacity(
                                approved, "REFERENCE_AUDIO", min(3, p.max_reference_audio)
                            )
                        ),
                        "max_total_reference_files": min(12, p.max_total_reference_files),
                        "supports_source_video": is_director and record.mode in {"v2v", "rv2v"},
                        "supports_motion_context": is_director and features["motion_context"],
                        "supports_refine": is_director and features["refine"],
                        "supports_face_refine": is_director and features["face_refine"],
                        "supports_audio": is_director,
                        "director_settings": [
                            case["settings"]
                            for case in verified_settings(record.profile, **settings_context)
                        ]
                        if is_director
                        else [],
                        "audio_modes": qualified_audio_modes(record.profile, **settings_context)
                        if is_director
                        else ["generate"],
                        "clip_min_seconds": 2,
                        "clip_max_seconds": 15,
                        "category_total_max_seconds": 15,
                        "reference_video_fps": 24,
                        "audio_requires_visual_reference": True,
                    }
                )
        except AppError as exc:
            disabled.append({**identity, "reason": exc.message, "code": exc.code})
    return GenerationCapabilitiesDTO(
        available=bool(combinations),
        combinations=combinations,
        disabled=disabled,
        source_capabilities=source_capabilities(),
        qualified_capabilities=qualified_capabilities(combinations),
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
    video = await session.get(Video, video_id, with_for_update=True)
    if video is None:
        raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
    query = (
        select(Scene)
        .where(Scene.video_id == video.id)
        .order_by(Scene.scene_order, Scene.id)
        .with_for_update()
    )
    all_scenes = list((await session.scalars(query)).all())
    semantic_inputs = await capture_batch_inputs(session, video, all_scenes)
    # Replay uses the saved scene set, including scenes now automatically selected.
    # Lock order remains video -> scenes -> idempotency, matching batch creation.
    existing = await session.scalar(
        select(IdempotencyKey)
        .where(
            IdempotencyKey.user_id == user.id,
            IdempotencyKey.operation == f"generate-all:{video_id}",
            IdempotencyKey.key == key,
        )
        .with_for_update()
    )
    saved = existing.response.get("_batch") if existing and existing.expires_at > utcnow() else None
    if saved is not None:
        if not await unchanged_batch_inputs(session, saved["inputs"], semantic_inputs):
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED", "Batch editor inputs changed since this action", 409
            )
        _, replay = await claim(
            session,
            user_id=user.id,
            operation=f"generate-all:{video_id}",
            key=key,
            payload={
                "request": replay_batch_request(payload, existing, saved["inputs"]),
                "inputs": saved["inputs"],
            },
            hours=settings.idempotency_hours,
        )
        return BatchGenerationDTO.model_validate(replay)
    scenes = [scene for scene in all_scenes if scene.enabled]
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
        require_complete_continuity_selection(requested_ids, all_scenes)
        scenes = [by_id[scene_id] for scene_id in requested_ids]
    else:
        eligible = []
        for scene in scenes:
            if not await is_selected_generation_fresh(session, scene, video):
                eligible.append(scene)
        scenes = expand_continuity_selection(eligible, all_scenes)

    record, replay = await claim(
        session,
        user_id=user.id,
        operation=f"generate-all:{video_id}",
        key=key,
        payload={
            "request": payload.model_dump(mode="json", exclude_unset=True),
            "inputs": semantic_inputs,
        },
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        return BatchGenerationDTO.model_validate(replay)
    if payload.expected_video_revision is not None:
        expected_scenes = {
            str(scene_id): revision
            for scene_id, revision in payload.expected_scene_revisions.items()
        }
        actual_scenes = {scene.id: scene.revision for scene in scenes}
        if video.revision != payload.expected_video_revision or expected_scenes != actual_scenes:
            raise AppError(
                "REVISION_CONFLICT",
                "The video or eligible scene revisions changed; reload before generating",
                409,
                {"video_revision": video.revision, "scene_revisions": actual_scenes},
            )
    if not scenes:
        raise AppError("NO_SCENES_TO_GENERATE", "No eligible scenes need generation", 409)

    service = GenerationService(settings)
    # Execute in video order even when the client selected IDs in picker order.
    scenes.sort(key=lambda scene: (scene.scene_order, scene.id))
    effective = [service.effective_request(scene, payload.settings) for scene in scenes]
    groups = plan_execution_groups(scenes, all_scenes, effective)
    generation_rows = []
    prepared_groups = []
    # Freeze and qualify EVERY group before adding any generation/run rows.
    for group in groups:
        members = []
        for scene in group.scenes:
            members.append(
                await service.create(
                    session,
                    scene_id=scene.id,
                    request=payload.settings,
                    user_id=user.id,
                    request_id=_request_id(request),
                    defer_aggregate_qualification=group.execution_scope == "aggregate",
                    persist=False,
                )
            )
        run = (
            await create_director_run(
                session,
                members,
                group.scenes,
                video=video,
                user_id=user.id,
                request_id=_request_id(request),
                persist=False,
            )
            if group.execution_scope == "aggregate"
            else None
        )
        prepared_groups.append((group, members, run))
        generation_rows.extend(members)
    for generation in generation_rows:
        await service.persist_prepared(session, generation, video)
    for _, _, run in prepared_groups:
        if run is not None:
            await persist_prepared_director_run(session, run)
    runs = [run for _, _, run in prepared_groups if run is not None]
    response = BatchGenerationDTO(
        video_id=video.id,
        generations=[GenerationDTO.model_validate(row) for row in generation_rows],
        director_run_id=runs[0].id if len(runs) == 1 else None,
        execution_groups=[
            ExecutionGroupDTO(
                execution_scope=group.execution_scope,
                scene_ids=[scene.id for scene in group.scenes],
                generation_ids=[row.id for row in members],
                director_run_id=run.id if run else None,
            )
            for group, members, run in prepared_groups
        ],
    )
    complete(
        record, {**response.model_dump(mode="json"), "_batch": {"inputs": semantic_inputs}}, 202
    )
    return response


def replay_batch_request(payload: GenerateAll, existing: IdempotencyKey, inputs: dict) -> dict:
    """Recognize historical full-default fingerprints only for the exact saved request."""
    explicit = payload.model_dump(mode="json", exclude_unset=True)
    added_fields = {
        "quality_profile",
        "aspect_ratio",
        "seed_policy",
        "source_video_asset_id",
        "cfg",
        "fps",
        "frames",
        "motion_context",
        "refine",
        "face_refine",
        "audio_policy",
        "timeline",
    }
    if added_fields & payload.settings.model_fields_set:
        return explicit
    legacy = payload.model_dump(mode="json")
    for name in added_fields:
        legacy["settings"].pop(name)
    if "steps" not in payload.settings.model_fields_set:
        legacy["settings"]["steps"] = 8
    if existing.request_hash == payload_hash({"request": legacy, "inputs": inputs}):
        return legacy
    return explicit


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
    member = await session.scalar(
        select(DirectorRunMember).where(DirectorRunMember.scene_generation_id == generation_id)
    )
    if member is not None:
        attempts = list(
            (
                await session.scalars(
                    select(DirectorRunAttempt)
                    .where(DirectorRunAttempt.director_run_id == member.director_run_id)
                    .order_by(DirectorRunAttempt.attempt_no, DirectorRunAttempt.id)
                )
            ).all()
        )
        return [
            GenerationAttemptDTO(
                id=row.id,
                generation_id=generation_id,
                attempt_no=row.attempt_no,
                status=row.status,
                client_id=row.client_id,
                comfy_prompt_id=row.comfy_prompt_id,
                error_code=row.error_code,
                error_message=row.error_message,
                created_at=row.created_at,
                finished_at=row.finished_at,
            )
            for row in attempts
        ]
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
    video_id = await session.scalar(
        select(SceneGeneration.video_id)
        .where(SceneGeneration.id == generation_id)
        .execution_options(autoflush=False)
    )
    if video_id is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    video = await lock_revisioned_row(session, Video, video_id)
    member = await session.scalar(
        select(DirectorRunMember)
        .where(DirectorRunMember.scene_generation_id == generation_id)
        .execution_options(autoflush=False)
    )
    if member is not None:
        run = await lock_revisioned_row(session, DirectorRun, member.director_run_id)
        if run is None or video is None:
            raise AppError("DIRECTOR_RUN_NOT_FOUND", "Director run was not found", 404)
        if run.status not in GENERATION_TERMINAL:
            cancelled = run.status == "CREATED"
            run.status = "CANCELLED" if cancelled else "CANCEL_REQUESTED"
            run.phase = "CANCELLED" if cancelled else "CANCELLING"
            run.revision += 1
            run.progress_updated_at = utcnow()
            if cancelled:
                run.finished_at = utcnow()
                run.claimed_by = None
                run.lease_expires_at = None
            members = list(
                (
                    await session.scalars(
                        select(DirectorRunMember)
                        .where(DirectorRunMember.director_run_id == run.id)
                        .order_by(DirectorRunMember.member_index)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            for item in members:
                current = await lock_revisioned_row(
                    session, SceneGeneration, item.scene_generation_id
                )
                if current and current.status not in GENERATION_TERMINAL:
                    current.status = run.status
                    current.phase = run.phase
                    current.revision += 1
                    current.progress_updated_at = run.progress_updated_at
                    if cancelled:
                        current.finished_at = run.finished_at
                        item.status = "CANCELLED"
        await refresh_video(session, video.id)
        generation = await lock_revisioned_row(session, SceneGeneration, generation_id)
        return GenerationDTO.model_validate(generation)
    generation = await lock_revisioned_row(session, SceneGeneration, generation_id)
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
