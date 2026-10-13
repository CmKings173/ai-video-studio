"""Resolve an explainable generation configuration from executed workflow evidence.

`resolve_generation_configuration` is the narrow shared API for preview and history
consumers. It never invents workflows or settings: only enabled, graph-integrity-checked
Director workflows with matching qualification evidence can be reported as runnable.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import WorkflowRecord
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.graph_identity import is_director_graph
from apps.api.app.providers.minimax_h3_director.output_canvas import (
    resolve_director_output_canvas,
)
from apps.api.app.schemas.api import (
    DirectorAudioPolicy,
    DirectorFaceRefine,
    DirectorMotionContext,
    DirectorRefine,
    GenerationMode,
    GenerationRatio,
    QualityProfile,
    ResolvedGenerationConfigurationDTO,
)
from apps.api.app.services.generation_intent import stable_hash
from apps.api.app.services.generation_resolution_adapter import format_resolved_resolution
from apps.api.app.services.workflow_contracts import (
    approved_record,
    profile_hash,
    resolve_qualified_generation_combination,
)
from apps.api.app.services.workflow_qualification import (
    ExecutionEvidence,
    qualification_for_combination,
)
from apps.api.app.services.workflow_registry import ApprovedWorkflow
from apps.api.app.services.workflow_router import director_task_for_mode, require_director_execution

ExecutionScope = Literal["single_scene", "aggregate"]
SeedPolicy = Literal["RANDOM", "FIXED"]
_SETTING_MODELS = {
    "motion_context": DirectorMotionContext,
    "refine": DirectorRefine,
    "face_refine": DirectorFaceRefine,
    "audio_policy": DirectorAudioPolicy,
}


def _normalize_director_settings(value: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = {} if value is None else value
    if not isinstance(raw, Mapping):
        raise ValueError("director_settings must be a mapping of the four supported setting groups")
    unknown = set(raw) - set(_SETTING_MODELS)
    if unknown:
        raise ValueError(f"Unsupported Director setting group(s): {', '.join(sorted(unknown))}")
    normalized: dict[str, dict[str, Any]] = {}
    for name, model in _SETTING_MODELS.items():
        settings = raw.get(name, {})
        if not isinstance(settings, Mapping):
            raise ValueError(f"Director setting group {name!r} must be an object")
        normalized[name] = model.model_validate(dict(settings), strict=True).model_dump(mode="json")
    return normalized


def _failure(
    *,
    requested_mode: str,
    quality_profile: str,
    aspect_ratio: str,
    execution_scope: str,
    seed_policy: str,
    fixed_seed: int | None,
    reason: str,
    status: str = "UNAVAILABLE",
    record: WorkflowRecord | None = None,
) -> ResolvedGenerationConfigurationDTO:
    return ResolvedGenerationConfigurationDTO(
        requested_mode=requested_mode,
        resolved_mode=record.mode if record is not None else None,
        execution_scope=execution_scope,  # type: ignore[arg-type]
        workflow_id=str(record.id) if record is not None else None,
        workflow_code=record.code if record is not None else None,
        workflow_version=record.version if record is not None else None,
        workflow_hash=record.workflow_hash if record is not None else None,
        quality_profile=quality_profile,  # type: ignore[arg-type]
        aspect_ratio=aspect_ratio,  # type: ignore[arg-type]
        seed_policy=seed_policy,  # type: ignore[arg-type]
        fixed_seed_or_null=fixed_seed if seed_policy == "FIXED" else None,
        qualification_status=status,
        runnable=False,
        unavailable_reason=reason,
    )


def _runtime_identity(evidence: Any) -> str | None:
    if evidence is None:
        return None
    return (
        f"ComfyUI {evidence.comfyui_version} ({evidence.comfyui_commit[:12]}); "
        f"{evidence.gpu_model} ×{evidence.gpu_count}; CUDA {evidence.cuda_runtime}; "
        f"driver {evidence.driver_version}"
    )


def _ordered_candidates(
    records: Sequence[WorkflowRecord], compatible_modes: Sequence[str]
) -> list[WorkflowRecord]:
    priority = {mode: index for index, mode in enumerate(compatible_modes)}
    # The partial unique index permits one enabled row per mode/profile/scope. These
    # final keys still make ordering stable if stale or test-created rows are present.
    return sorted(
        records,
        key=lambda record: (
            priority.get(record.mode, len(priority)),
            record.code or "",
            record.version or "",
            str(record.id),
        ),
    )


def _require_aggregate_spec_matches(
    spec: DirectorExecutionSpec | None,
    *,
    record: WorkflowRecord,
    approved: ApprovedWorkflow,
    requested_mode: str,
    task: str,
    quality_profile: str,
    aspect_ratio: str,
    duration_seconds: float,
    seed_policy: str,
    fixed_seed: int | None,
    director_settings: Mapping[str, Any],
    resolved_width: int,
    resolved_height: int,
    resolved_frames: int,
    resolved_duration_seconds: float,
) -> None:
    if spec is None:
        raise AppError(
            "DIRECTOR_AGGREGATE_CONTEXT_REQUIRED",
            "Aggregate resolution requires the actual frozen execution spec and continuity list",
            409,
        )
    if not isinstance(spec, DirectorExecutionSpec):
        raise AppError(
            "DIRECTOR_AGGREGATE_CONTEXT_INVALID",
            "Aggregate resolution requires a validated Director execution spec",
            409,
        )
    expected = (
        spec.workflow_id == str(record.id),
        spec.workflow_code == record.code,
        spec.workflow_version == record.version,
        spec.workflow_hash == record.workflow_hash == approved.workflow_hash,
        spec.slot_map_hash == record.slot_map_hash == approved.slot_map_hash,
        spec.profile_hash == profile_hash(record.profile),
        spec.task == task,
        spec.requested_mode == requested_mode,
        spec.quality_profile == quality_profile,
        (spec.canvas.width, spec.canvas.height) == (resolved_width, resolved_height),
        spec.canvas.aspect_ratio == aspect_ratio,
        spec.steps == record.profile.get("steps"),
        spec.cfg == record.profile.get("cfg"),
        spec.fps == record.profile.get("fps"),
        spec.frames == resolved_frames,
        spec.requested_duration_seconds == duration_seconds,
        spec.resolved_duration_seconds == resolved_duration_seconds,
        spec.seed_policy == seed_policy,
        seed_policy != "FIXED" or spec.seed == fixed_seed,
        spec.motion_context == director_settings["motion_context"],
        spec.refine == director_settings["refine"],
        spec.face_refine == director_settings["face_refine"],
        spec.audio_policy == director_settings["audio_policy"],
    )
    if not all(expected):
        raise AppError(
            "DIRECTOR_AGGREGATE_CONTEXT_MISMATCH",
            "Frozen aggregate execution differs from the requested qualified configuration",
            409,
        )


async def resolve_generation_configuration(
    session: AsyncSession,
    *,
    requested_mode: GenerationMode,
    quality_profile: QualityProfile,
    aspect_ratio: GenerationRatio,
    duration_seconds: float,
    execution_scope: ExecutionScope = "single_scene",
    workflow_id: str | None = None,
    seed_policy: SeedPolicy = "RANDOM",
    fixed_seed: int | None = None,
    director_settings: Mapping[str, Any] | None = None,
    aggregate_spec: DirectorExecutionSpec | None = None,
    aggregate_continuities: Sequence[str] | None = None,
    aggregate_preparation: bool = False,
) -> ResolvedGenerationConfigurationDTO:
    """Resolve one requested configuration into the existing response DTO.

    Signature contract for B2:
    - `requested_mode`, `quality_profile`, `aspect_ratio`, `duration_seconds`,
      `execution_scope`, and seed fields describe the caller's exact request.
    - `workflow_id` pins selection when supplied; the resolver never silently falls
      back to another workflow for a pinned id.
    - `director_settings` accepts only motion_context/refine/face_refine/audio_policy;
      omitted groups use the current API schema defaults.
    - `aggregate_preparation=True` is an internal, non-runnable candidate path for
      aggregate member preparation only. It requires aggregate scope and no frozen
      spec; the exact group qualification remains pending and is never inferred here.
    - Aggregate resolution is runnable only with the actual frozen `aggregate_spec`
      and matching `aggregate_continuities`; those inputs are checked against the
      persisted aggregate qualification evidence.
    - `configuration_hash` identifies the resolved workflow/profile/canvas/timing,
      exact settings and seed policy (including a fixed seed when requested). It does
      not include prompt text, assets, runtime-generated random seeds, or UI labels.
    """
    if not math.isfinite(duration_seconds) or not 4 <= duration_seconds <= 15:
        raise ValueError("duration_seconds must be finite and between 4 and 15")
    if seed_policy not in {"RANDOM", "FIXED"}:
        raise ValueError("seed_policy must be RANDOM or FIXED")
    if seed_policy == "FIXED" and (
        type(fixed_seed) is not int or not 0 <= fixed_seed <= 9_007_199_254_740_991
    ):
        raise ValueError("FIXED seed_policy requires a safe non-negative fixed_seed")
    if seed_policy == "RANDOM" and fixed_seed is not None:
        raise ValueError("fixed_seed must be omitted when seed_policy is RANDOM")
    if execution_scope not in {"single_scene", "aggregate"}:
        raise ValueError("execution_scope must be single_scene or aggregate")
    if type(aggregate_preparation) is not bool:
        raise ValueError("aggregate_preparation must be a boolean")
    if aggregate_preparation and execution_scope != "aggregate":
        raise ValueError("aggregate_preparation is only valid for aggregate scope")
    if aggregate_preparation and aggregate_spec is not None:
        raise ValueError("aggregate_preparation cannot be used with a frozen aggregate spec")

    settings = _normalize_director_settings(director_settings)
    try:
        task = director_task_for_mode(requested_mode)
    except ValueError as exc:
        return _failure(
            requested_mode=requested_mode,
            quality_profile=quality_profile,
            aspect_ratio=aspect_ratio,
            execution_scope=execution_scope,
            seed_policy=seed_policy,
            fixed_seed=fixed_seed,
            reason=str(exc),
        )
    compatible_modes = tuple(dict.fromkeys((task, requested_mode)))

    if workflow_id is not None:
        record = await session.get(WorkflowRecord, str(workflow_id))
        if record is None:
            return _failure(
                requested_mode=requested_mode,
                quality_profile=quality_profile,
                aspect_ratio=aspect_ratio,
                execution_scope=execution_scope,
                seed_policy=seed_policy,
                fixed_seed=fixed_seed,
                reason="The requested workflow does not exist",
            )
        records = [record]
    else:
        records = list(
            (
                await session.scalars(
                    select(WorkflowRecord).where(
                        WorkflowRecord.enabled.is_(True),
                        WorkflowRecord.mode.in_(compatible_modes),
                        WorkflowRecord.quality_profile == quality_profile,
                        WorkflowRecord.execution_scope == execution_scope,
                    )
                )
            ).all()
        )
    if not records:
        return _failure(
            requested_mode=requested_mode,
            quality_profile=quality_profile,
            aspect_ratio=aspect_ratio,
            execution_scope=execution_scope,
            seed_policy=seed_policy,
            fixed_seed=fixed_seed,
            reason=(
                f"No enabled {execution_scope} workflow is configured for "
                f"{requested_mode}/{quality_profile}/{aspect_ratio}"
            ),
        )

    failure_summaries: list[ResolvedGenerationConfigurationDTO] = []
    for record in _ordered_candidates(records, compatible_modes):
        base_status = "UNAVAILABLE"
        summary = _failure(
            requested_mode=requested_mode,
            quality_profile=quality_profile,
            aspect_ratio=aspect_ratio,
            execution_scope=execution_scope,
            seed_policy=seed_policy,
            fixed_seed=fixed_seed,
            reason="Workflow has not passed generation qualification",
            record=record,
        )
        profile = record.profile if isinstance(record.profile, dict) else {}
        qualification, _ = qualification_for_combination(
            profile,
            record.workflow_hash,
            record.slot_map_hash,
            mode=record.mode,
            quality_profile=quality_profile,
            aspect_ratio=aspect_ratio,
        )
        base_status = qualification.lifecycle
        summary.qualification_status = base_status
        aggregate_case: dict[str, Any] | None = None

        try:
            if not record.enabled:
                raise AppError("WORKFLOW_NOT_APPROVED", "Workflow is not enabled", 409)
            if record.mode not in compatible_modes:
                raise AppError(
                    "WORKFLOW_MODE_CONFLICT",
                    "Workflow mode does not match the requested generation mode",
                    422,
                )
            if record.quality_profile != quality_profile:
                raise AppError(
                    "WORKFLOW_QUALITY_CONFLICT",
                    "Workflow quality profile does not match the request",
                    422,
                )
            if record.execution_scope != execution_scope:
                raise AppError(
                    "WORKFLOW_EXECUTION_SCOPE_CONFLICT",
                    "Workflow execution scope does not match the request",
                    422,
                )
            if not is_director_graph(record.workflow):
                raise AppError(
                    "WORKFLOW_RETIRED",
                    "Only qualified MiniMax H3 Director workflows can run new generations",
                    409,
                )
            require_director_execution(record)
            approved = approved_record(record)
            if (
                approved.workflow_hash != record.workflow_hash
                or approved.slot_map_hash != record.slot_map_hash
            ):
                raise AppError(
                    "WORKFLOW_INTEGRITY_FAILED",
                    "Approved workflow content no longer matches its stored hashes",
                    409,
                )
            if not qualification.qualified:
                raise AppError(
                    "WORKFLOW_NOT_QUALIFIED",
                    qualification.reason or "Exact requested combination is not qualified",
                    409,
                )

            resolved = resolve_qualified_generation_combination(
                record,
                approved,
                quality_profile=quality_profile,
                aspect_ratio=aspect_ratio,
                duration_seconds=duration_seconds,
            )
            expected_width, expected_height = resolve_director_output_canvas(
                (resolved.width, resolved.height), settings["refine"], task=task
            )

            if execution_scope == "single_scene":
                from apps.api.app.providers.minimax_h3_director.qualification import (
                    require_settings,
                    verified_settings,
                )

                require_settings(
                    record.profile,
                    settings,
                    base_canvas=(resolved.width, resolved.height),
                    task=task,
                )
                uses_optional_settings = (
                    any(
                        settings[name]["enabled"]
                        for name in ("motion_context", "refine", "face_refine")
                    )
                    or settings["audio_policy"].get("mode", "generate") != "generate"
                )
                runtime_evidence = (
                    qualification.evidence.model_copy(
                        update={"output": resolved.combination.output}
                    )
                    if qualification.evidence is not None
                    else None
                )
                if uses_optional_settings:
                    matching_cases = [
                        case
                        for case in verified_settings(
                            record.profile,
                            base_canvas=(resolved.width, resolved.height),
                            task=task,
                        )
                        if case.get("settings") == settings
                    ]
                    if len(matching_cases) != 1:
                        raise AppError(
                            "DIRECTOR_SETTINGS_EVIDENCE_AMBIGUOUS",
                            "Exact optional settings need one unambiguous executed evidence case",
                            409,
                        )
                    runtime_evidence = ExecutionEvidence.model_validate(matching_cases[0])
            elif aggregate_preparation:
                # Generate All prepares member intents before its exact run spec exists.
                # Resolve only the member's enabled workflow/profile/ratio/canvas facts;
                # never claim runtime or group qualification from this candidate.
                runtime_evidence = None
            else:
                if aggregate_continuities is None:
                    raise AppError(
                        "DIRECTOR_AGGREGATE_CONTEXT_REQUIRED",
                        "Aggregate resolution requires the actual continuity boundary list",
                        409,
                    )
                _require_aggregate_spec_matches(
                    aggregate_spec,
                    record=record,
                    approved=approved,
                    requested_mode=requested_mode,
                    task=task,
                    quality_profile=quality_profile,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    seed_policy=seed_policy,
                    fixed_seed=fixed_seed,
                    director_settings=settings,
                    resolved_width=resolved.width,
                    resolved_height=resolved.height,
                    resolved_frames=resolved.frames,
                    resolved_duration_seconds=resolved.duration_seconds,
                )
                from apps.api.app.services.director_run_service import (
                    require_director_aggregate_qualification,
                )

                aggregate_case = require_director_aggregate_qualification(
                    record.profile,
                    aggregate_spec,
                    member_count=aggregate_spec.member_count,
                    continuities=aggregate_continuities,
                )
                runtime_evidence = ExecutionEvidence.model_validate(aggregate_case)

            if runtime_evidence is None and not aggregate_preparation:
                raise AppError(
                    "WORKFLOW_NOT_QUALIFIED", "Executed runtime evidence is missing", 409
                )
            if runtime_evidence is not None and (
                runtime_evidence.output.width,
                runtime_evidence.output.height,
            ) != (
                expected_width,
                expected_height,
            ):
                raise AppError(
                    "WORKFLOW_OUTPUT_CANVAS_MISMATCH",
                    "Measured output dimensions differ from the resolved Director canvas",
                    409,
                )

            profile_data = record.profile
            requested_settings = {
                "motion_context": settings["motion_context"],
                "refine": settings["refine"],
                "face_refine": settings["face_refine"],
                "audio_policy": settings["audio_policy"],
            }
            configuration_identity = {
                "requested_mode": requested_mode,
                "resolved_mode": record.mode,
                "execution_scope": execution_scope,
                "workflow_id": str(record.id),
                "workflow_version": record.version,
                "workflow_hash": record.workflow_hash,
                "profile_hash": profile_hash(profile_data),
                "quality_profile": quality_profile,
                "aspect_ratio": aspect_ratio,
                "native_canvas": [resolved.width, resolved.height],
                "expected_output_canvas": [expected_width, expected_height],
                "steps": resolved.profile.steps,
                "cfg": profile_data.get("cfg"),
                "sampler": resolved.profile.sampler,
                "scheduler": resolved.profile.scheduler,
                "fps": resolved.profile.fps,
                "frames": resolved.frames,
                "duration_seconds": resolved.duration_seconds,
                "seed_policy": seed_policy,
                "fixed_seed": fixed_seed if seed_policy == "FIXED" else None,
                "director_settings": requested_settings,
                "aggregate_settings_hash": (
                    aggregate_case.get("settings_hash") if aggregate_case is not None else None
                ),
            }
            if aggregate_preparation:
                # Candidate hashes are intentionally distinct from final qualified runs.
                configuration_identity["aggregate_preparation_candidate"] = True
            configuration_hash = stable_hash(configuration_identity)
            quality_label = profile_data.get("quality_label")
            if not isinstance(quality_label, str) or not quality_label.strip():
                quality_label = record.quality_profile
            quality_description = profile_data.get("quality_description")
            if not isinstance(quality_description, str) or not quality_description.strip():
                quality_description = None
            resolution_label = (
                format_resolved_resolution(
                    {
                        "expected_output_width": expected_width,
                        "expected_output_height": expected_height,
                    }
                )
                or f"{expected_width} × {expected_height} px"
            )

            return ResolvedGenerationConfigurationDTO(
                requested_mode=requested_mode,
                resolved_mode=record.mode,
                execution_scope=execution_scope,
                workflow_id=str(record.id),
                workflow_code=record.code,
                workflow_version=record.version,
                workflow_hash=record.workflow_hash,
                profile_hash=profile_hash(profile_data),
                quality_profile=record.quality_profile,
                quality_label=quality_label,
                quality_description=quality_description,
                aspect_ratio=aspect_ratio,
                native_width=resolved.width,
                native_height=resolved.height,
                expected_output_width=expected_width,
                expected_output_height=expected_height,
                resolution_display_label=resolution_label,
                sampling_steps=resolved.profile.steps,
                cfg=profile_data.get("cfg"),
                sampler=resolved.profile.sampler or None,
                scheduler=resolved.profile.scheduler or None,
                runtime_identity=(
                    _runtime_identity(runtime_evidence) if runtime_evidence is not None else None
                ),
                model_identity=(
                    f"sha256:{runtime_evidence.model_hash}"
                    if runtime_evidence is not None
                    else None
                ),
                fps=resolved.profile.fps,
                frames=resolved.frames,
                duration_seconds=resolved.duration_seconds,
                seed_policy=seed_policy,
                fixed_seed_or_null=fixed_seed if seed_policy == "FIXED" else None,
                refine_enabled=settings["refine"]["enabled"],
                upscale_enabled=(
                    settings["refine"]["enabled"]
                    and settings["refine"]["mode"] in {"upscale", "latent_upscale"}
                ),
                refine_mode=settings["refine"]["mode"],
                qualification_status=(
                    "AGGREGATE_PREPARATION" if aggregate_preparation else qualification.lifecycle
                ),
                runnable=not aggregate_preparation,
                unavailable_reason=(
                    "Candidate only; exact group qualification is pending the frozen "
                    "Director execution spec and continuity validation"
                    if aggregate_preparation
                    else None
                ),
                configuration_hash=configuration_hash,
            )
        except AppError as exc:
            summary.unavailable_reason = exc.message
            summary.qualification_status = base_status
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            summary.unavailable_reason = str(exc) or "Workflow configuration could not be resolved"
            summary.qualification_status = base_status
        failure_summaries.append(summary)

    # All candidates were examined before choosing the deterministic explanation;
    # an incompatible first row can never mask a later eligible workflow.
    return failure_summaries[0]
