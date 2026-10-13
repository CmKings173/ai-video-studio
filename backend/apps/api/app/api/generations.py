from __future__ import annotations

import re
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.common import pagination
from apps.api.app.api.deps import (
    AuthPrincipal,
    idempotency_key,
    require_csrf,
    require_editor,
)
from apps.api.app.api.generation_replay import (
    _reauthorize_batch_replay,
    _reauthorize_generation_replay,
)
from apps.api.app.api.ownership import (
    get_owned,
    get_video_reference_asset,
    owner_scope,
    principal_id,
)
from apps.api.app.api.ownership import (
    is_admin as principal_is_admin,
)
from apps.api.app.core.config import Settings, get_settings
from apps.api.app.core.errors import AppError
from apps.api.app.db.locking import lock_revisioned_row
from apps.api.app.db.models import (
    DirectorRun,
    DirectorRunAttempt,
    DirectorRunMember,
    GenerationAttempt,
    IdempotencyKey,
    Scene,
    SceneGeneration,
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
from apps.api.app.providers.minimax_h3_director.qualification import verified_settings
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
    ResolvedGenerationConfigurationDTO,
    VariationRequest,
)
from apps.api.app.services.batch_identity import capture_batch_inputs, unchanged_batch_inputs
from apps.api.app.services.continuity_groups import (
    expand_continuity_selection,
    require_complete_continuity_selection,
)
from apps.api.app.services.deletion_state import is_logically_deleted
from apps.api.app.services.director_run_service import (
    create_director_run,
    persist_prepared_director_run,
)
from apps.api.app.services.execution_groups import plan_execution_groups
from apps.api.app.services.generation_display import resolve_generation_configuration
from apps.api.app.services.generation_freshness import is_selected_generation_fresh
from apps.api.app.services.generation_service import GenerationService
from apps.api.app.services.idempotency import claim, complete, payload_hash
from apps.api.app.services.prompt_engine import PromptEngine
from apps.api.app.services.video_dependency_guard import (
    DependencySnapshot,
    lock_video_dependencies,
    read_video_dependencies,
)
from apps.api.app.services.workflow_contracts import approved_record, native_asset_slots
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


def _attach_resolved_configuration(result: GenerationDTO) -> GenerationDTO:
    configuration = (result.input_snapshot or {}).get("resolved_configuration")
    if isinstance(configuration, dict):
        result.resolved_configuration = ResolvedGenerationConfigurationDTO.model_validate(
            configuration
        )
    return result


def _generation_dto(generation: SceneGeneration) -> GenerationDTO:
    return _attach_resolved_configuration(GenerationDTO.model_validate(generation))


def _require_replay_preview_hash(payload: GenerationRequest, replay: dict) -> None:
    if payload.execution_prompt is None:
        return
    supplied_hash = getattr(payload, "preview_configuration_hash", None)
    snapshot = replay.get("input_snapshot")
    expected_hash = (
        snapshot.get("resolved_configuration_hash") if isinstance(snapshot, dict) else None
    )
    if (
        not isinstance(supplied_hash, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", supplied_hash) is None
        or not isinstance(expected_hash, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", expected_hash) is None
        or not secrets.compare_digest(supplied_hash.lower(), expected_hash.lower())
    ):
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE",
            "The accepted prompt preview is missing or no longer matches the saved "
            "generation configuration; refresh the preview",
            409,
        )


def _batch_preview_digest(manifest: dict) -> str:
    unsigned = {
        key: value for key, value in manifest.items() if key != "preview_configuration_hash"
    }
    return payload_hash({"generate_all_preview_v1": unsigned})


def _require_batch_preview_hash(payload: GenerateAll, manifest: dict) -> None:
    supplied = payload.settings.preview_configuration_hash
    expected = manifest.get("preview_configuration_hash")
    if (
        not isinstance(supplied, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", supplied) is None
        or not isinstance(expected, str)
        or not secrets.compare_digest(supplied.lower(), expected.lower())
    ):
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE",
            "The accepted Generate All preview is missing or no longer matches the selected "
            "scenes and resolved configurations; refresh the preview",
            409,
        )


def _require_batch_replay_preview_hash(payload: GenerateAll, replay: dict) -> None:
    saved_batch = replay.get("_batch")
    manifest = saved_batch.get("preview") if isinstance(saved_batch, dict) else None
    if not isinstance(manifest, dict):
        raise AppError("PREVIEW_CONFIGURATION_STALE", "Saved batch has no preview contract", 409)
    saved_hash = manifest.get("preview_configuration_hash")
    supplied = payload.settings.preview_configuration_hash
    if (
        not isinstance(saved_hash, str)
        or not secrets.compare_digest(saved_hash, _batch_preview_digest(manifest))
        or not isinstance(supplied, str)
        or re.fullmatch(r"[a-fA-F0-9]{64}", supplied) is None
        or not secrets.compare_digest(supplied.lower(), saved_hash.lower())
    ):
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE",
            "The accepted Generate All preview no longer matches the saved batch; "
            "refresh the preview",
            409,
        )
    anchor_scene = manifest.get("anchor_scene_revision")
    video_revision = manifest.get("video_revision")
    if payload.settings.execution_prompt is not None and (
        payload.settings.source_scene_revision != anchor_scene
        or payload.settings.source_video_revision != video_revision
    ):
        raise AppError("PROMPT_PREVIEW_STALE", "Scene or video changed after batch preview", 412)
    generations = replay.get("generations")
    if not isinstance(generations, list) or not generations:
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE", "Saved batch has no prepared generations", 409
        )
    expected_scene_ids = manifest.get("scene_ids")
    preview_entries = manifest.get("scenes")
    group_entries = manifest.get("execution_groups")
    if not isinstance(preview_entries, list) or not isinstance(group_entries, list):
        raise AppError("PREVIEW_CONFIGURATION_STALE", "Saved batch preview is malformed", 409)
    expected_config_hashes = {
        entry.get("scene_id"): entry.get("configuration_hash")
        for entry in preview_entries
        if isinstance(entry, dict)
    }
    aggregate_settings_by_scene: dict[str, str | None] = {}
    for group in group_entries:
        if not isinstance(group, dict):
            raise AppError("PREVIEW_CONFIGURATION_STALE", "Saved batch groups are malformed", 409)
        scene_ids = group.get("scene_ids")
        hashes = group.get("generation_configuration_hashes")
        scope = group.get("execution_scope")
        aggregate_hash = group.get("aggregate_settings_hash")
        if (
            scope not in {"single_scene", "aggregate"}
            or not isinstance(scene_ids, list)
            or not isinstance(hashes, list)
            or len(scene_ids) != len(hashes)
            or (
                scope == "aggregate"
                and (
                    not isinstance(aggregate_hash, str)
                    or re.fullmatch(r"[a-f0-9]{64}", aggregate_hash) is None
                )
            )
            or (scope == "single_scene" and aggregate_hash is not None)
        ):
            raise AppError(
                "PREVIEW_CONFIGURATION_STALE", "Saved batch group contract is invalid", 409
            )
        for scene_id, configuration_hash in zip(scene_ids, hashes, strict=True):
            if (
                scene_id in aggregate_settings_by_scene
                or expected_config_hashes.get(scene_id) != configuration_hash
            ):
                raise AppError(
                    "PREVIEW_CONFIGURATION_STALE", "Saved batch group membership changed", 409
                )
            aggregate_settings_by_scene[scene_id] = aggregate_hash
    if (
        replay.get("video_id") != manifest.get("video_id")
        or not isinstance(expected_scene_ids, list)
        or set(aggregate_settings_by_scene) != set(expected_scene_ids)
        or list(expected_config_hashes) != expected_scene_ids
        or [generation.get("scene_id") for generation in generations] != expected_scene_ids
    ):
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE",
            "Saved generations do not match the accepted batch scene manifest",
            409,
        )
    for generation in generations:
        snapshot = generation.get("input_snapshot") if isinstance(generation, dict) else None
        scene_id = generation.get("scene_id") if isinstance(generation, dict) else None
        expected_config_hash = expected_config_hashes.get(scene_id)
        configuration = (
            snapshot.get("resolved_configuration") if isinstance(snapshot, dict) else None
        )
        expected_aggregate_hash = aggregate_settings_by_scene.get(scene_id)
        if (
            not isinstance(snapshot, dict)
            or not isinstance(expected_config_hash, str)
            or snapshot.get("resolved_configuration_hash") != expected_config_hash
            or not isinstance(configuration, dict)
            or configuration.get("runnable") is not True
            or configuration.get("configuration_hash") != expected_config_hash
            or snapshot.get("aggregate_settings_hash") != expected_aggregate_hash
            or not secrets.compare_digest(
                str(snapshot.get("batch_preview_configuration_hash", "")), saved_hash
            )
        ):
            raise AppError(
                "PREVIEW_CONFIGURATION_STALE",
                "Saved generation snapshots do not match their accepted per-scene configurations",
                409,
            )


def _validate_configuration_overrides(
    request: GenerationRequest,
    workflow: WorkflowRecord,
    configuration: ResolvedGenerationConfigurationDTO,
) -> None:
    if request.steps is not None and request.steps != configuration.sampling_steps:
        raise AppError("GENERATION_INPUT_INVALID", "Raw steps contradict qualified profile", 422)
    if request.fps is not None and request.fps != configuration.fps:
        raise AppError(
            "GENERATION_INPUT_INVALID", "Requested FPS contradicts qualified profile", 422
        )
    if request.frames is not None and request.frames != configuration.frames:
        raise AppError(
            "GENERATION_INPUT_INVALID", "Requested frame count contradicts qualified profile", 422
        )
    profile_cfg = float(workflow.profile.get("cfg", 1.0))
    if request.cfg is not None and request.cfg != profile_cfg:
        raise AppError(
            "GENERATION_INPUT_INVALID", "Requested CFG contradicts qualified profile", 422
        )


async def _select_generate_all_scenes(
    session: AsyncSession,
    payload: GenerateAll,
    all_scenes: list[Scene],
    video: Video,
) -> list[Scene]:
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
    scenes.sort(key=lambda scene: (scene.scene_order, scene.id))
    return scenes


async def _resolve_batch_member_configuration(
    session: AsyncSession,
    service: GenerationService,
    scene: Scene,
    video: Video,
    request: GenerationRequest,
    *,
    allow_aggregate: bool = False,
) -> tuple[WorkflowRecord, ResolvedGenerationConfigurationDTO]:
    effective = GenerationService.effective_request(scene, request)
    GenerationService.require_exact_workflow_choice(effective)
    service._require_motion_scope(effective, allow_aggregate)
    routing = RoutingInput(
        requested_mode=effective.mode,
        first_frame_asset_id=str(effective.first_frame_asset_id)
        if effective.first_frame_asset_id
        else None,
        last_frame_asset_id=str(effective.last_frame_asset_id)
        if effective.last_frame_asset_id
        else None,
        reference_image_asset_ids=service._ids(effective.reference_image_asset_ids),
        reference_video_asset_ids=service._ids(effective.reference_video_asset_ids),
        reference_audio_asset_ids=service._ids(effective.reference_audio_asset_ids),
        source_video_asset_id=str(effective.source_video_asset_id)
        if effective.source_video_asset_id
        else None,
    )
    try:
        mode = select_mode(routing)
    except WorkflowRoutingError as exc:
        raise AppError("GENERATION_INPUT_INVALID", str(exc), 422) from exc
    ratio = effective.aspect_ratio or video.aspect_ratio
    quality = effective.quality_profile or "STANDARD"
    workflow_record, _, configuration = await service._workflow(
        session,
        mode,
        effective.workflow_id,
        quality,
        ratio,
        requested_mode=effective.mode or "AUTO",
        requested_width=effective.width,
        requested_height=effective.height,
        execution_settings=service._director_execution_settings(effective),
        duration_seconds=float(scene.duration_seconds),
        seed_policy=service._effective_seed_policy(effective),
        seed=effective.seed,
        allow_aggregate=allow_aggregate,
    )
    _validate_configuration_overrides(effective, workflow_record, configuration)
    if allow_aggregate:
        if configuration.runnable or configuration.qualification_status != "AGGREGATE_PREPARATION":
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                "Aggregate member preparation did not return a non-runnable candidate",
                409,
            )
    elif not configuration.runnable:
        raise AppError(
            "WORKFLOW_NOT_QUALIFIED",
            configuration.unavailable_reason or "No qualified workflow matches the request",
            409,
        )
    return workflow_record, configuration


async def _build_batch_preview_manifest(
    session: AsyncSession,
    *,
    settings: Settings,
    payload: GenerateAll,
    video: Video,
    scenes: list[Scene],
    all_scenes: list[Scene],
) -> dict:
    if payload.expected_video_revision is not None:
        expected_scenes = {
            str(scene_id): revision
            for scene_id, revision in payload.expected_scene_revisions.items()
        }
        actual_scenes = {scene.id: scene.revision for scene in scenes}
        if video.revision != payload.expected_video_revision or expected_scenes != actual_scenes:
            raise AppError(
                "REVISION_CONFLICT",
                "The video or eligible scene revisions changed; reload before previewing",
                409,
                {"video_revision": video.revision, "scene_revisions": actual_scenes},
            )
    if not scenes:
        raise AppError("NO_SCENES_TO_GENERATE", "No eligible scenes need generation", 409)
    effective_by_scene = {
        scene.id: GenerationService.effective_request(scene, payload.settings) for scene in scenes
    }
    groups = plan_execution_groups(
        scenes, all_scenes, [effective_by_scene[scene.id] for scene in scenes]
    )
    anchor = scenes[0]
    if payload.settings.execution_prompt is not None and (
        payload.settings.source_scene_revision != anchor.revision
        or payload.settings.source_video_revision != video.revision
    ):
        raise AppError(
            "PROMPT_PREVIEW_STALE",
            "The accepted prompt preview does not match the first selected scene and current video",
            412,
            {"scene_revision": anchor.revision, "video_revision": video.revision},
        )
    service = GenerationService(settings)
    entries = []
    for group in groups:
        for scene in group.scenes:
            workflow, configuration = await _resolve_batch_member_configuration(
                session,
                service,
                scene,
                video,
                payload.settings,
                allow_aggregate=group.execution_scope == "aggregate",
            )
            if not configuration.configuration_hash:
                raise AppError(
                    "WORKFLOW_NOT_QUALIFIED",
                    "The resolved scene configuration has no stable identity",
                    409,
                )
            entry = {
                "scene_id": scene.id,
                "scene_revision": scene.revision,
                "duration_seconds": float(scene.duration_seconds),
                "execution_scope": group.execution_scope,
                "workflow_id": workflow.id,
                "requested_workflow_id": (
                    str(effective_by_scene[scene.id].workflow_id)
                    if effective_by_scene[scene.id].workflow_id is not None
                    else None
                ),
                "_candidate_configuration": configuration.model_dump(mode="json"),
            }
            entries.append(entry)
    manifest = {
        "contract_version": 1,
        "hash_contract": "generate_all_preview_v1",
        "video_id": video.id,
        "video_revision": video.revision,
        "anchor_scene_id": anchor.id,
        "anchor_scene_revision": anchor.revision,
        "scene_ids": [scene.id for scene in scenes],
        "scenes": entries,
        "execution_groups": [
            {
                "execution_scope": group.execution_scope,
                "scene_ids": [scene.id for scene in group.scenes],
                "continuities": [
                    (scene.spec or {}).get("continuity", "CUT") for scene in group.scenes
                ],
            }
            for group in groups
        ],
    }
    return manifest


async def _prepare_batch_preview_generations(
    session: AsyncSession,
    *,
    settings: Settings,
    payload: GenerateAll,
    video: Video,
    scenes: list[Scene],
    all_scenes: list[Scene],
    manifest: dict,
    user: AuthPrincipal,
    deterministic_seed: bool,
    request_id: str | None = None,
) -> tuple[dict, list[tuple[Any, list[SceneGeneration], Any | None]]]:
    """Prepare every selected member/run without persistence and return only final DTOs."""
    service = GenerationService(settings)
    effective = [GenerationService.effective_request(scene, payload.settings) for scene in scenes]
    groups = plan_execution_groups(scenes, all_scenes, effective)
    candidates = {entry["scene_id"]: entry for entry in manifest["scenes"]}
    prepared_groups: list[tuple[Any, list[SceneGeneration], Any | None]] = []

    for group in groups:
        members: list[SceneGeneration] = []
        for scene in group.scenes:
            entry = candidates[scene.id]
            candidate = ResolvedGenerationConfigurationDTO.model_validate(
                entry["_candidate_configuration"]
            )
            member_request = payload.settings.model_copy(
                update={
                    "workflow_id": UUID(candidate.workflow_id),
                    "aspect_ratio": candidate.aspect_ratio,
                    "width": candidate.native_width,
                    "height": candidate.native_height,
                    "source_scene_revision": scene.revision,
                    "source_video_revision": video.revision,
                    "preview_configuration_hash": (
                        None
                        if group.execution_scope == "aggregate"
                        else candidate.configuration_hash
                    ),
                },
                deep=True,
            )
            use_probe_seed = (
                deterministic_seed
                and GenerationService._effective_seed_policy(member_request) == "RANDOM"
                and member_request.seed is None
            )
            generation = await service.create(
                session,
                scene_id=scene.id,
                request=member_request,
                user_id=principal_id(user),
                request_id=request_id,
                defer_aggregate_qualification=group.execution_scope == "aggregate",
                persist=False,
                is_admin=principal_is_admin(user),
                preflight_seed=0 if use_probe_seed else None,
            )
            snapshot = generation.input_snapshot
            actual_configuration = (
                snapshot.get("resolved_configuration") if isinstance(snapshot, dict) else None
            )
            actual_hash = (
                snapshot.get("resolved_configuration_hash") if isinstance(snapshot, dict) else None
            )
            expected_candidate_state = group.execution_scope == "aggregate"
            if (
                not isinstance(actual_configuration, dict)
                or actual_hash != candidate.configuration_hash
                or actual_configuration.get("runnable") is expected_candidate_state
                or actual_configuration.get("workflow_id") != candidate.workflow_id
                or actual_configuration.get("workflow_hash") != candidate.workflow_hash
            ):
                raise AppError(
                    "WORKFLOW_CONFIGURATION_STALE",
                    "Generation preparation differs from the resolved batch candidate",
                    409,
                )
            members.append(generation)

        run = None
        if group.execution_scope == "aggregate":
            run = await create_director_run(
                session,
                members,
                group.scenes,
                video=video,
                user_id=principal_id(user),
                is_admin=principal_is_admin(user),
                request_id=request_id,
                persist=False,
            )
            GenerationService.finalize_aggregate_member_configurations(members, run)
        prepared_groups.append((group, members, run))

    prepared_by_scene = {
        generation.scene_id: generation
        for _, members, _ in prepared_groups
        for generation in members
    }
    final_scenes = []
    for scene in scenes:
        generation = prepared_by_scene[scene.id]
        snapshot = generation.input_snapshot
        configuration = snapshot.get("resolved_configuration")
        configuration_hash = snapshot.get("resolved_configuration_hash")
        if (
            not isinstance(configuration, dict)
            or configuration.get("runnable") is not True
            or not isinstance(configuration_hash, str)
            or configuration.get("configuration_hash") != configuration_hash
        ):
            raise AppError(
                "WORKFLOW_NOT_QUALIFIED",
                "Prepared batch member has no final qualified configuration",
                409,
            )
        candidate = candidates[scene.id]
        final_scenes.append(
            {
                "scene_id": scene.id,
                "scene_revision": scene.revision,
                "duration_seconds": float(scene.duration_seconds),
                "execution_scope": candidate["execution_scope"],
                "workflow_id": candidate["workflow_id"],
                "requested_workflow_id": candidate["requested_workflow_id"],
                "resolved_configuration": configuration,
                "configuration_hash": configuration_hash,
            }
        )

    final_groups = []
    for group, members, run in prepared_groups:
        group_entry = {
            "execution_scope": group.execution_scope,
            "scene_ids": [scene.id for scene in group.scenes],
            "generation_configuration_hashes": [
                generation.input_snapshot["resolved_configuration_hash"] for generation in members
            ],
            "continuities": [(scene.spec or {}).get("continuity", "CUT") for scene in group.scenes],
            "member_timings": [
                {
                    "scene_id": generation.scene_id,
                    "duration_seconds": generation.input_snapshot["director_execution"][
                        "requested_duration_seconds"
                    ],
                    "frames": generation.input_snapshot["director_execution"]["frames"],
                    "fps": generation.input_snapshot["director_execution"]["fps"],
                    "resolved_duration_seconds": generation.input_snapshot["director_execution"][
                        "resolved_duration_seconds"
                    ],
                }
                for generation in members
            ],
        }
        if run is not None:
            group_entry["aggregate_settings_hash"] = run.input_snapshot["aggregate_settings_hash"]
        final_groups.append(group_entry)

    final_manifest = {
        key: value for key, value in manifest.items() if key not in {"scenes", "execution_groups"}
    }
    final_manifest["scenes"] = final_scenes
    final_manifest["execution_groups"] = final_groups
    final_manifest["preview_configuration_hash"] = _batch_preview_digest(final_manifest)
    return final_manifest, prepared_groups


@router.post("/videos/{video_id}/generate-all/preview")
async def preview_generate_all(
    video_id: str,
    payload: GenerateAll,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    video = await get_owned(
        session,
        Video,
        video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    await _validate_video_dependencies(session, video, user)
    GenerationService.require_exact_workflow_choice(payload.settings)
    await _validate_generation_references(session, [payload.settings], user, video=video)
    dependencies = await lock_video_dependencies(
        session, video, user_id=principal_id(user), is_admin=principal_is_admin(user)
    )
    video = dependencies.video
    query = (
        select(Scene)
        .where(Scene.video_id == video.id, owner_scope(Scene, user))
        .order_by(Scene.scene_order, Scene.id)
        .with_for_update()
    )
    all_scenes = list((await session.scalars(query)).all())
    effective_by_scene = {
        scene.id: GenerationService.effective_request(scene, payload.settings)
        for scene in all_scenes
    }
    await _validate_generation_references(
        session, list(effective_by_scene.values()), user, video=video
    )
    scenes = await _select_generate_all_scenes(session, payload, all_scenes, video)
    manifest = await _build_batch_preview_manifest(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
    )
    manifest, _ = await _prepare_batch_preview_generations(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
        manifest=manifest,
        user=user,
        deterministic_seed=True,
    )
    await session.rollback()
    return manifest


async def _validate_generation_references(
    session: AsyncSession,
    requests: list[GenerationRequest],
    user: AuthPrincipal,
    *,
    video: Video,
    scene_id: str | None = None,
) -> None:
    asset_ids: set[str] = set()
    for request in requests:
        for field in ("first_frame_asset_id", "last_frame_asset_id", "source_video_asset_id"):
            asset_id = getattr(request, field)
            if asset_id is not None:
                asset_ids.add(str(asset_id))
        for field in (
            "reference_image_asset_ids",
            "reference_video_asset_ids",
            "reference_audio_asset_ids",
        ):
            asset_ids.update(str(asset_id) for asset_id in getattr(request, field))
        if request.parent_generation_id is not None:
            # Derivatives execute the parent's frozen inputs, not today's Scene
            # config. Read canonical columns and only the declared manifest.
            with session.no_autoflush:
                parent = (
                    (
                        await session.execute(
                            select(*SceneGeneration.__table__.c).where(
                                SceneGeneration.id == str(request.parent_generation_id),
                                owner_scope(SceneGeneration, user),
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            if (
                parent is None
                or parent["video_id"] != video.id
                or (scene_id is not None and parent["scene_id"] != scene_id)
            ):
                raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
            role_fields = {
                "FIRST_FRAME": "first_frame_asset_id",
                "LAST_FRAME": "last_frame_asset_id",
                "SOURCE_VIDEO": "source_video_asset_id",
                "REFERENCE_IMAGE": "reference_image_asset_ids",
                "REFERENCE_AUDIO": "reference_audio_asset_ids",
                "REFERENCE_VIDEO": "reference_video_asset_ids",
            }
            for binding in (parent["input_snapshot"] or {}).get("assets", []):
                field = role_fields.get(binding.get("role"))
                if field is not None and field not in request.model_fields_set:
                    asset_ids.add(str(binding["id"]))

    if asset_ids:
        for asset_id in sorted(asset_ids):
            await get_video_reference_asset(session, video, asset_id, user)


async def _validate_video_dependencies(
    session: AsyncSession,
    video: Video,
    user: AuthPrincipal,
) -> tuple[DependencySnapshot | None, DependencySnapshot | None]:
    """Validate active parent context before external work or idempotency."""
    dependencies = await read_video_dependencies(
        session, video, user_id=principal_id(user), is_admin=principal_is_admin(user)
    )
    return dependencies.product, dependencies.effective_brand


@router.post("/scenes/{scene_id}/prompt-preview", response_model=PromptPreviewDTO)
async def preview_prompt(
    scene_id: str,
    payload: PromptPreviewRequest | None = Body(default=None),
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PromptPreviewDTO:
    scene = await get_owned(
        session,
        Scene,
        scene_id,
        user,
        error_code="SCENE_NOT_FOUND",
        message="Scene was not found",
    )
    video = await get_owned(
        session,
        Video,
        scene.video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    product, brand = await _validate_video_dependencies(session, video, user)
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
    supplied = payload if isinstance(payload, PromptPreviewRequest) else GenerationRequest()
    GenerationService.require_exact_workflow_choice(supplied)
    effective = GenerationService.effective_request(scene, supplied)
    await _validate_generation_references(
        session, [effective], user, video=video, scene_id=scene.id
    )
    service = GenerationService(settings)
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
    service.require_exact_workflow_choice(effective)
    seed_policy = service._effective_seed_policy(effective)
    workflow, approved, configuration = await service._workflow(
        session,
        mode,
        effective.workflow_id,
        effective.quality_profile or "STANDARD",
        effective.aspect_ratio or video.aspect_ratio,
        requested_mode=effective.mode or "AUTO",
        requested_width=effective.width,
        requested_height=effective.height,
        execution_settings=service._director_execution_settings(effective),
        duration_seconds=float(scene.duration_seconds),
        seed_policy=seed_policy,
        seed=effective.seed,
    )
    _validate_configuration_overrides(effective, workflow, configuration)
    assets, _, _ = await service._load_assets(
        session,
        effective,
        video,
        user_id=principal_id(user),
        is_admin=principal_is_admin(user),
    )
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
        resolved_configuration=configuration,
    )


async def _create(
    *,
    scene_id: str,
    payload: GenerationRequest,
    request: Request,
    key: str,
    user: AuthPrincipal,
    session: AsyncSession,
    settings: Settings,
    operation_name: str,
) -> GenerationDTO:
    scene = await get_owned(
        session,
        Scene,
        scene_id,
        user,
        error_code="SCENE_NOT_FOUND",
        message="Scene was not found",
    )
    video = await get_owned(
        session,
        Video,
        scene.video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    await _validate_video_dependencies(session, video, user)
    effective = (
        payload
        if payload.operation in {"VARIATION", "REGENERATE"}
        else GenerationService.effective_request(scene, payload)
    )
    await _validate_generation_references(
        session, [effective], user, video=video, scene_id=scene.id
    )
    record, replay = await claim(
        session,
        user_id=principal_id(user),
        operation=f"{operation_name}:{scene_id}",
        key=key,
        payload=payload.model_dump(mode="json", exclude_unset=True),
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        _require_replay_preview_hash(payload, replay)
        response = await _reauthorize_generation_replay(
            session,
            replay,
            scene=scene,
            video=video,
            user=user,
        )
        return _attach_resolved_configuration(response)
    service = GenerationService(settings)
    generation = await service.create(
        session,
        scene_id=scene_id,
        request=payload,
        user_id=principal_id(user),
        request_id=_request_id(request),
        is_admin=principal_is_admin(user),
    )
    await session.flush()
    response = _generation_dto(generation)
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
    user: AuthPrincipal = Depends(require_csrf),
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
    user: AuthPrincipal = Depends(require_csrf),
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
    user: AuthPrincipal = Depends(require_csrf),
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
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> GenerationCapabilitiesDTO:
    combinations: list[dict] = []
    disabled: list[dict] = []
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
    defaults = GenerationService._director_execution_settings(GenerationRequest())
    for record in records:
        identity = {
            "workflow_id": record.id,
            "mode": record.mode,
            "quality_profile": record.quality_profile,
            "execution_scope": record.execution_scope,
        }
        if not record.enabled:
            disabled.append(
                {**identity, "reason": "Workflow is disabled", "code": "WORKFLOW_NOT_APPROVED"}
            )
            continue
        if record.profile.get("retired"):
            disabled.append(
                {**identity, "reason": "Workflow is retired", "code": "WORKFLOW_RETIRED"}
            )
            continue
        if not is_director_graph(record.workflow):
            disabled.append(
                {
                    **identity,
                    "reason": "Only qualified Director workflows can run",
                    "code": "WORKFLOW_RETIRED",
                }
            )
            continue

        profile = record.profile if isinstance(record.profile, dict) else {}
        evidence = profile.get("execution_evidence")
        evidence_combinations = evidence.get("combinations") if isinstance(evidence, dict) else None
        if not isinstance(evidence_combinations, list) or not evidence_combinations:
            disabled.append(
                {
                    **identity,
                    "reason": "Executed canvas evidence is unavailable",
                    "code": "WORKFLOW_NOT_QUALIFIED",
                }
            )
            continue
        try:
            approved = approved_record(record)
            limits = GenerationService._profile(record)
            task = director_task_for_mode(record.mode)
        except (AppError, WorkflowRoutingError) as exc:
            disabled.append(
                {
                    **identity,
                    "reason": exc.message if isinstance(exc, AppError) else str(exc),
                    "code": exc.code if isinstance(exc, AppError) else "WORKFLOW_NOT_QUALIFIED",
                }
            )
            continue

        any_runnable = False
        first_failure: tuple[str, str] | None = None
        seen_canvases: set[tuple[str, int, int]] = set()
        for case in evidence_combinations:
            if not isinstance(case, dict):
                continue
            ratio = case.get("aspect_ratio")
            width, height = case.get("width"), case.get("height")
            if (
                not isinstance(ratio, str)
                or type(width) is not int
                or type(height) is not int
                or width <= 0
                or height <= 0
                or (ratio, width, height) in seen_canvases
            ):
                continue
            seen_canvases.add((ratio, width, height))
            candidate_settings = [defaults]
            setting_keys = {repr(sorted(defaults.items()))}
            for settings_case in verified_settings(
                profile, base_canvas=(width, height), task=task
            ):
                setting_values = settings_case.get("settings")
                if not isinstance(setting_values, dict):
                    continue
                setting_key = repr(sorted(setting_values.items()))
                if setting_key not in setting_keys:
                    setting_keys.add(setting_key)
                    candidate_settings.append(setting_values)

            qualified_choices: list[dict] = []
            for director_settings in candidate_settings:
                try:
                    normalized_settings = GenerationService._director_execution_settings(
                        GenerationRequest.model_validate(director_settings)
                    )
                    configuration = await resolve_generation_configuration(
                        session,
                        requested_mode=record.mode,
                        quality_profile=record.quality_profile,
                        aspect_ratio=ratio,
                        duration_seconds=5.0,
                        execution_scope=record.execution_scope,
                        workflow_id=str(record.id),
                        seed_policy="RANDOM",
                        fixed_seed=None,
                        director_settings=normalized_settings,
                    )
                except AppError as exc:
                    first_failure = first_failure or (exc.code, exc.message)
                    continue
                except ValueError as exc:
                    first_failure = first_failure or (
                        "WORKFLOW_SETTINGS_INVALID",
                        str(exc),
                    )
                    continue
                if (
                    not configuration.runnable
                    or configuration.workflow_id != str(record.id)
                    or configuration.native_width != width
                    or configuration.native_height != height
                    or configuration.expected_output_width is None
                    or configuration.expected_output_height is None
                    or (
                        not isinstance(configuration.configuration_hash, str)
                        or re.fullmatch(r"[a-fA-F0-9]{64}", configuration.configuration_hash)
                        is None
                    )
                ):
                    first_failure = first_failure or (
                        "WORKFLOW_NOT_QUALIFIED",
                        configuration.unavailable_reason or "Canvas/settings are not qualified",
                    )
                    continue
                qualified_choices.append(
                    {
                        "settings": normalized_settings,
                        "resolved_configuration": configuration.model_dump(mode="json"),
                        "qualification_status": configuration.qualification_status,
                        "unavailable_reason": configuration.unavailable_reason,
                    }
                )

            if not qualified_choices:
                continue
            any_runnable = True
            base_choice = next(
                (choice for choice in qualified_choices if choice["settings"] == defaults),
                qualified_choices[0],
            )
            configuration = ResolvedGenerationConfigurationDTO.model_validate(
                base_choice["resolved_configuration"]
            )
            combinations.append(
                {
                    **identity,
                    "aspect_ratio": ratio,
                    "width": width,
                    "height": height,
                    "native_width": configuration.native_width,
                    "native_height": configuration.native_height,
                    "resolved_width": configuration.native_width,
                    "resolved_height": configuration.native_height,
                    "expected_output_width": configuration.expected_output_width,
                    "expected_output_height": configuration.expected_output_height,
                    "steps": configuration.sampling_steps,
                    "fps": configuration.fps,
                    "frames": configuration.frames,
                    "workflow_version": configuration.workflow_version,
                    "workflow_hash": configuration.workflow_hash,
                    "provider": "minimax_h3_director",
                    "director_task": task,
                    "runnable": True,
                    "qualification_status": configuration.qualification_status,
                    "unavailable_reason": None,
                    "resolved_configuration": configuration.model_dump(mode="json"),
                    "qualified_settings": qualified_choices,
                    "execution_scope": record.execution_scope,
                    "required_asset_slots": sorted(native_asset_slots(approved)),
                    "max_reference_images": min(9, limits.max_reference_images),
                    "max_reference_videos": (
                        0 if record.mode == "rv2v" else min(3, limits.max_reference_videos)
                    ),
                    "max_reference_audio": min(3, limits.max_reference_audio),
                    "max_total_reference_files": min(12, limits.max_total_reference_files),
                    "supports_source_video": record.mode in {"v2v", "rv2v"},
                    "supports_motion_context": any(
                        choice["settings"]["motion_context"].get("enabled")
                        for choice in qualified_choices
                    ),
                    "supports_refine": any(
                        choice["settings"]["refine"].get("enabled") for choice in qualified_choices
                    ),
                    "supports_face_refine": any(
                        choice["settings"]["face_refine"].get("enabled")
                        for choice in qualified_choices
                    ),
                    "supports_audio": any(
                        choice["settings"]["audio_policy"].get("mode", "generate") != "mute"
                        for choice in qualified_choices
                    ),
                    "director_settings": [choice["settings"] for choice in qualified_choices],
                    "audio_modes": sorted(
                        {
                            choice["settings"]["audio_policy"].get("mode", "generate")
                            for choice in qualified_choices
                        }
                    ),
                    "clip_min_seconds": 2,
                    "clip_max_seconds": 15,
                    "category_total_max_seconds": 15,
                    "reference_video_fps": 24,
                    "audio_requires_visual_reference": True,
                }
            )
        if not any_runnable:
            code, reason = first_failure or (
                "WORKFLOW_NOT_QUALIFIED",
                "No exact qualified canvas/settings choice is runnable",
            )
            disabled.append({**identity, "reason": reason, "code": code})
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
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BatchGenerationDTO:
    video = await get_owned(
        session,
        Video,
        video_id,
        user,
        error_code="VIDEO_NOT_FOUND",
        message="Video was not found",
    )
    await _validate_video_dependencies(session, video, user)
    GenerationService.require_exact_workflow_choice(payload.settings)
    await _validate_generation_references(session, [payload.settings], user, video=video)
    dependencies = await lock_video_dependencies(
        session, video, user_id=principal_id(user), is_admin=principal_is_admin(user)
    )
    video = dependencies.video
    query = (
        select(Scene)
        .where(Scene.video_id == video.id, owner_scope(Scene, user))
        .order_by(Scene.scene_order, Scene.id)
        .with_for_update()
    )
    all_scenes = list((await session.scalars(query)).all())
    effective_by_scene = {
        scene.id: GenerationService.effective_request(scene, payload.settings)
        for scene in all_scenes
    }
    await _validate_generation_references(
        session, list(effective_by_scene.values()), user, video=video
    )
    semantic_inputs = await capture_batch_inputs(session, video, all_scenes)
    # Replay uses the saved scene set, including scenes now automatically selected.
    # Parent dependencies were locked before scenes; idempotency follows children.
    existing = await session.scalar(
        select(IdempotencyKey)
        .where(
            IdempotencyKey.user_id == principal_id(user),
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
            user_id=principal_id(user),
            operation=f"generate-all:{video_id}",
            key=key,
            payload={
                "request": replay_batch_request(payload, existing, saved["inputs"]),
                "inputs": saved["inputs"],
            },
            hours=settings.idempotency_hours,
        )
        _require_batch_replay_preview_hash(payload, replay)
        response = await _reauthorize_batch_replay(session, replay, video=video, user=user)
        response.generations = [
            _attach_resolved_configuration(item) for item in response.generations
        ]
        return response
    scenes = await _select_generate_all_scenes(session, payload, all_scenes, video)
    record, replay = await claim(
        session,
        user_id=principal_id(user),
        operation=f"generate-all:{video_id}",
        key=key,
        payload={
            "request": payload.model_dump(mode="json", exclude_unset=True),
            "inputs": semantic_inputs,
        },
        hours=settings.idempotency_hours,
    )
    if replay is not None:
        _require_batch_replay_preview_hash(payload, replay)
        response = await _reauthorize_batch_replay(session, replay, video=video, user=user)
        response.generations = [
            _attach_resolved_configuration(item) for item in response.generations
        ]
        return response

    manifest_seed = await _build_batch_preview_manifest(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
    )
    preview_manifest, _ = await _prepare_batch_preview_generations(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
        manifest=manifest_seed,
        user=user,
        deterministic_seed=True,
    )
    _require_batch_preview_hash(payload, preview_manifest)

    actual_manifest_seed = await _build_batch_preview_manifest(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
    )
    actual_manifest, prepared_groups = await _prepare_batch_preview_generations(
        session,
        settings=settings,
        payload=payload,
        video=video,
        scenes=scenes,
        all_scenes=all_scenes,
        manifest=actual_manifest_seed,
        user=user,
        deterministic_seed=False,
        request_id=_request_id(request),
    )
    _require_batch_preview_hash(payload, actual_manifest)
    if (
        actual_manifest["preview_configuration_hash"]
        != preview_manifest["preview_configuration_hash"]
    ):
        raise AppError(
            "PREVIEW_CONFIGURATION_STALE",
            "The batch configuration changed between preview verification "
            "and final seed assignment",
            409,
        )

    generation_rows = [generation for _, members, _ in prepared_groups for generation in members]
    # Freeze the accepted batch identity into member and aggregate snapshots only
    # after every group has passed non-persisting qualification.
    for _, members, run in prepared_groups:
        for generation in members:
            GenerationService.bind_batch_preview_configuration_hash(
                generation,
                actual_manifest["preview_configuration_hash"],
                run=run,
            )
    service = GenerationService(settings)
    for _, members, run in prepared_groups:
        for generation in members:
            await service.persist_prepared(session, generation, video, director_run=run)
    for _, _, run in prepared_groups:
        if run is not None:
            await persist_prepared_director_run(session, run)
    runs = [run for _, _, run in prepared_groups if run is not None]
    response = BatchGenerationDTO(
        video_id=video.id,
        generations=[_generation_dto(row) for row in generation_rows],
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
        record,
        {
            **response.model_dump(mode="json"),
            "_batch": {"inputs": semantic_inputs, "preview": actual_manifest},
        },
        202,
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
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> Page[GenerationDTO]:
    await get_owned(
        session,
        Scene,
        scene_id,
        user,
        error_code="SCENE_NOT_FOUND",
        message="Scene was not found",
    )
    page, size = paging
    where = (SceneGeneration.scene_id == scene_id, owner_scope(SceneGeneration, user))
    total = (
        await session.scalar(select(func.count()).select_from(SceneGeneration).where(*where)) or 0
    )
    rows = list(
        (
            await session.scalars(
                select(SceneGeneration)
                .where(*where)
                .order_by(SceneGeneration.generation_no.desc(), SceneGeneration.id.desc())
                .offset((page - 1) * size)
                .limit(size)
            )
        ).all()
    )
    return Page(
        items=[_generation_dto(row) for row in rows],
        total=total,
        page=page,
        page_size=size,
    )


@router.get("/generations/{generation_id}", response_model=GenerationDTO)
async def get_generation(
    generation_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> GenerationDTO:
    generation = await get_owned(
        session,
        SceneGeneration,
        generation_id,
        user,
        error_code="GENERATION_NOT_FOUND",
        message="Generation was not found",
    )
    return _generation_dto(generation)


@router.get(
    "/generations/{generation_id}/attempts",
    response_model=list[GenerationAttemptDTO],
)
async def list_generation_attempts(
    generation_id: str,
    user: AuthPrincipal = Depends(require_editor),
    session: AsyncSession = Depends(get_session),
) -> list[GenerationAttemptDTO]:
    await get_owned(
        session,
        SceneGeneration,
        generation_id,
        user,
        error_code="GENERATION_NOT_FOUND",
        message="Generation was not found",
    )
    member = await session.scalar(
        select(DirectorRunMember).where(
            DirectorRunMember.scene_generation_id == generation_id,
            owner_scope(DirectorRunMember, user),
        )
    )
    if member is not None:
        attempts = list(
            (
                await session.scalars(
                    select(DirectorRunAttempt)
                    .where(
                        DirectorRunAttempt.director_run_id == member.director_run_id,
                        owner_scope(DirectorRunAttempt, user),
                    )
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
                .where(
                    GenerationAttempt.generation_id == generation_id,
                    owner_scope(GenerationAttempt, user),
                )
                .order_by(GenerationAttempt.attempt_no, GenerationAttempt.id)
            )
        ).all()
    )
    return [GenerationAttemptDTO.model_validate(row) for row in rows]


@router.post("/generations/{generation_id}/cancel", response_model=GenerationDTO)
async def cancel_generation(
    generation_id: str,
    user: AuthPrincipal = Depends(require_csrf),
    session: AsyncSession = Depends(get_session),
) -> GenerationDTO:
    video_id = await session.scalar(
        select(SceneGeneration.video_id)
        .where(
            SceneGeneration.id == generation_id,
            owner_scope(SceneGeneration, user),
        )
        .execution_options(autoflush=False)
    )
    if video_id is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    video = await lock_revisioned_row(session, Video, video_id)
    if video is None or await is_logically_deleted(session, "VIDEO", video_id):
        raise AppError("VIDEO_NOT_FOUND", "Video was not found", 404)
    scene_id = await session.scalar(
        select(SceneGeneration.scene_id).where(SceneGeneration.id == generation_id)
    )
    if (
        scene_id is None
        or await is_logically_deleted(session, "SCENE", scene_id, video_id)
        or await is_logically_deleted(session, "GENERATION", generation_id, video_id)
    ):
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    member = await session.scalar(
        select(DirectorRunMember)
        .where(
            DirectorRunMember.scene_generation_id == generation_id,
            owner_scope(DirectorRunMember, user),
        )
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
                        .where(
                            DirectorRunMember.director_run_id == run.id,
                            owner_scope(DirectorRunMember, user),
                        )
                        .order_by(DirectorRunMember.member_index)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).all()
            )
            for item in members:
                await get_owned(
                    session,
                    SceneGeneration,
                    item.scene_generation_id,
                    user,
                    error_code="GENERATION_NOT_FOUND",
                    message="Generation was not found",
                )
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
        return _generation_dto(generation)
    generation = await lock_revisioned_row(session, SceneGeneration, generation_id)
    if generation is None or video is None:
        raise AppError("GENERATION_NOT_FOUND", "Generation was not found", 404)
    if generation.status in GENERATION_TERMINAL:
        return _generation_dto(generation)
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
    return _generation_dto(generation)
