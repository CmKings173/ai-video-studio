"""Freeze one qualified Director execution from newly created scene generations.

The caller owns the transaction and idempotency response. Member generations stay
CREATED and retain their snapshots; the dispatcher must exclude run members from
independent admission. Aggregate evidence is separate from single-scene evidence.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    DirectorRun,
    DirectorRunMember,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
)
from apps.api.app.providers.minimax_h3_director.capabilities import DIRECTOR_SOURCE
from apps.api.app.providers.minimax_h3_director.contracts import (
    DirectorExecutionSpec,
    artifact_member_range,
)
from apps.api.app.providers.minimax_h3_director.output_canvas import resolve_director_output_canvas
from apps.api.app.schemas.api import (
    DirectorAudioPolicy,
    DirectorFaceRefine,
    DirectorMotionContext,
    DirectorRefine,
)
from apps.api.app.services.generation_intent import (
    GenerationAssetBinding,
    GenerationIntent,
    GenerationTimelineSegment,
    PromptProvenance,
    stable_hash,
)
from apps.api.app.services.qualification_binding import optional_weights_bound, provenance_matches
from apps.api.app.services.workflow_contracts import (
    approved_record,
    profile_hash,
    require_contract,
    require_frozen,
)
from apps.api.app.services.workflow_qualification import ExecutionEvidence, require_qualified


def _invalid(message: str) -> AppError:
    return AppError("DIRECTOR_AGGREGATE_INVALID", message, 422)


def _segment_exporters(profile: dict, member_count: int) -> list[dict[str, Any]]:
    if type(member_count) is not int or member_count < 1:
        raise _invalid("Aggregate member count must be a positive integer")
    bindings = profile.get("output_artifacts")
    if not isinstance(bindings, list) or not bindings:
        raise AppError(
            "DIRECTOR_AGGREGATE_NOT_QUALIFIED",
            "Aggregate execution requires explicit segment exporters for every member",
            409,
        )
    covered: list[int] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            raise _invalid("Artifact exporter bindings must be objects")
        if binding.get("role") != "segment":
            continue
        try:
            coverage = artifact_member_range(binding, member_count)
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
        if (
            ("coverage" not in binding and "member_index" not in binding)
            or binding.get("required", True) is not True
            or not isinstance(binding.get("node_id"), str)
            or not binding["node_id"]
        ):
            raise _invalid("Segment exporters require bounded, required member bindings")
        covered.extend(coverage)
    if sorted(covered) != list(range(member_count)):
        raise AppError(
            "DIRECTOR_AGGREGATE_NOT_QUALIFIED",
            "Segment exporters must cover each aggregate member exactly once",
            409,
        )
    return copy.deepcopy(bindings)


def director_aggregate_settings(
    spec: DirectorExecutionSpec,
    *,
    member_count: int,
    continuities: Sequence[str],
    output_artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonical qualification settings, independent of user content and run IDs.

    Evidence producers hash this object with stable_hash. Asset indices describe the
    ordered input shape; asset bytes, accepted text and the sampled seed remain bound
    by intent_hash/execution_hash instead. Exporter coverage is execution-significant.
    """
    return {
        "provider": spec.provider,
        "provider_version": spec.provider_version,
        "source_repository": spec.source_repository,
        "source_commit": spec.source_commit,
        "workflow_hash": spec.workflow_hash,
        "slot_map_hash": spec.slot_map_hash,
        "profile_hash": spec.profile_hash,
        "task": spec.task,
        "requested_mode": spec.requested_mode,
        "quality_profile": spec.quality_profile,
        "canvas": spec.canvas.model_dump(mode="json"),
        "steps": spec.steps,
        "cfg": spec.cfg,
        "fps": spec.fps,
        "seed_policy": spec.seed_policy,
        "frames": spec.frames,
        "requested_duration_seconds": spec.requested_duration_seconds,
        "resolved_duration_seconds": spec.resolved_duration_seconds,
        "member_count": member_count,
        "continuities": list(continuities),
        "timeline": [
            segment.model_dump(mode="json", exclude={"scene_id", "prompt"})
            for segment in spec.timeline
        ],
        "motion_context": copy.deepcopy(spec.motion_context),
        "refine": copy.deepcopy(spec.refine),
        "face_refine": copy.deepcopy(spec.face_refine),
        "audio_policy": copy.deepcopy(spec.audio_policy),
        "output_artifacts": copy.deepcopy(output_artifacts),
    }


def require_director_aggregate_qualification(
    profile: dict[str, Any],
    spec: DirectorExecutionSpec,
    *,
    member_count: int,
    continuities: Sequence[str],
) -> dict[str, Any]:
    """Return a matching, fully executed case; never infer aggregate qualification.

    Each director_aggregate_cases entry embeds ExecutionEvidence at its top level,
    plus member_count, task, continuities and settings_hash. An optional settings
    object, when present, must equal director_aggregate_settings exactly.
    """
    _require_supported_execution(spec)
    require_qualified(profile, spec.workflow_hash, spec.slot_map_hash)
    if spec.profile_hash != profile_hash(profile):
        raise AppError("DIRECTOR_AGGREGATE_NOT_QUALIFIED", "Aggregate profile changed", 409)
    if (
        type(member_count) is not int
        or member_count < 1
        or (spec.member_count is not None and spec.member_count != member_count)
        or len(continuities) != member_count
        or any(boundary not in {"CUT", "CONTINUOUS"} for boundary in continuities)
    ):
        raise _invalid("Aggregate qualification requires one boundary per member")
    exporters = _segment_exporters(profile, member_count)
    if any(binding.get("coverage") == "all_members" for binding in exporters):
        if spec.member_count != member_count:
            raise _invalid(
                "Dynamic aggregate qualification requires a frozen execution member count"
            )
    settings = director_aggregate_settings(
        spec, member_count=member_count, continuities=continuities, output_artifacts=exporters
    )
    identity = stable_hash(settings)
    raw_baseline = profile["execution_evidence"]
    cases = raw_baseline.get("director_aggregate_cases", [])
    if not isinstance(cases, list):
        cases = []
    for case in cases:
        if not isinstance(case, dict) or (
            type(case.get("member_count")) is not int
            or case["member_count"] != member_count
            or case.get("task") != spec.task
            or case.get("continuities") != list(continuities)
            or case.get("settings_hash") != identity
            or ("settings" in case and case["settings"] != settings)
        ):
            continue
        try:
            evidence = ExecutionEvidence.model_validate(case)
        except (ValidationError, TypeError):
            continue
        if (
            not evidence.executed
            or not evidence.output.has_video
            or not provenance_matches(profile, case)
            or not optional_weights_bound(profile, settings)
        ):
            continue
        if evidence.output.fps != spec.fps:
            continue
        expected_canvas = resolve_director_output_canvas(
            (spec.canvas.width, spec.canvas.height), spec.refine, task=spec.task
        )
        if (evidence.output.width, evidence.output.height) != expected_canvas:
            continue
        if spec.audio_policy.get("mode", "generate") != "mute" and not evidence.output.has_audio:
            continue
        return copy.deepcopy(case)
    raise AppError(
        "DIRECTOR_AGGREGATE_NOT_QUALIFIED",
        "Exact Director aggregate member count, timeline, settings and exporters "
        "require fully executed evidence",
        409,
        {"member_count": member_count, "task": spec.task, "settings_hash": identity},
    )


def _require_supported_execution(spec: DirectorExecutionSpec) -> None:
    if (
        spec.source_repository != DIRECTOR_SOURCE["repository"]
        or spec.source_commit != DIRECTOR_SOURCE["commit"]
    ):
        raise _invalid("Frozen Director execution must use the pinned provider source")
    try:
        for schema, values in (
            (DirectorMotionContext, spec.motion_context),
            (DirectorRefine, spec.refine),
            (DirectorFaceRefine, spec.face_refine),
            (DirectorAudioPolicy, spec.audio_policy),
        ):
            schema.model_validate(values, strict=True)
    except ValidationError as exc:
        raise _invalid("Frozen Director optional settings are unsupported") from exc
    if spec.task == "i2v" and spec.motion_context.get("enabled"):
        for segment in spec.timeline:
            has_anchor = (
                bool(segment.asset_indices.get("FIRST_FRAME"))
                if segment.asset_indices
                else any(asset.role == "FIRST_FRAME" for asset in spec.assets)
            )
            if segment.continuity_from_previous and has_anchor:
                raise AppError(
                    "DIRECTOR_CONTINUITY_CONFLICTS_WITH_EXPLICIT_FIRST_FRAME",
                    "Continuous I2V cannot preserve native Motion Context with an explicit "
                    "first-frame anchor; mixed I2V/T2V requires a separately qualified graph",
                    422,
                )


def _compatible_settings(spec: DirectorExecutionSpec) -> dict[str, Any]:
    return spec.model_dump(
        mode="json",
        include={
            "provider",
            "provider_version",
            "source_repository",
            "source_commit",
            "workflow_id",
            "workflow_code",
            "workflow_version",
            "workflow_hash",
            "slot_map_hash",
            "profile_hash",
            "custom_node_versions",
            "provenance",
            "task",
            "requested_mode",
            "quality_profile",
            "canvas",
            "steps",
            "cfg",
            "fps",
            "seed_policy",
            "negative_prompt",
            "motion_context",
            "refine",
            "face_refine",
            "audio_policy",
        },
    )


def _freeze_member(
    generation: SceneGeneration, scene: Scene, video: Video, workflow: WorkflowRecord
) -> tuple[dict[str, Any], GenerationIntent, DirectorExecutionSpec]:
    snapshot = copy.deepcopy(generation.input_snapshot)
    if (
        generation.status != "CREATED"
        or (
            generation.attempt_count != 0
            and not (inspect(generation).transient and generation.attempt_count is None)
        )
        or generation.comfy_prompt_id
        or generation.claimed_by
        or generation.video_id != video.id
        or scene.video_id != video.id
        or generation.scene_id != scene.id
        or not scene.enabled
        or snapshot.get("provider") != "minimax_h3_director"
        or snapshot.get("scene_revision") != scene.revision
        or snapshot.get("video_revision") != video.revision
    ):
        raise _invalid("Members must be fresh Director generations from the current scene/video")
    require_frozen(snapshot, workflow)
    try:
        intent = GenerationIntent.model_validate(snapshot["generation_intent"])
        spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
    except (KeyError, ValueError, TypeError) as exc:
        raise _invalid("Member Director intent/execution contract is invalid") from exc
    _require_supported_execution(spec)
    intent_values = intent.model_dump(mode="json", exclude={"schema_version", "provider_task"})
    spec_values = spec.model_dump(mode="json", include=set(intent_values))
    if (
        spec_values != intent_values
        or spec.task != intent.provider_task
        or spec.requested_mode != generation.mode
        or spec.workflow_id != generation.workflow_id
        or spec.resolved_duration_seconds != spec.frames / spec.fps
        or not intent.timeline
    ):
        raise _invalid("Member execution does not match its frozen intent")
    return snapshot, intent, spec


async def create_director_run(
    session: AsyncSession,
    generations: Sequence[SceneGeneration],
    scenes: Sequence[Scene],
    *,
    video: Video,
    user_id: str,
    request_id: str | None = None,
    persist: bool = True,
) -> DirectorRun:
    """Freeze a run and optionally persist it in the caller's existing transaction.

    Pass scenes in the same order as generations, after GenerationService.create.
    Validation/qualification completes before adding run rows. Propagate AppError so
    the caller rolls back the entire batch. Execution prompts/settings and member
    state are preserved; source-group association metadata is added to member
    snapshots and their frozen run clones before persistence.
    Random seeds use the first member's frozen draw; fixed seeds must agree. Segment
    prompts remain member-specific and the aggregate global prompt is empty.
    With persist=False, no run/member is added or flushed. After all batch groups
    qualify, persist_prepared_director_run inserts this exact frozen run and ID.
    """
    if not session.in_transaction():
        raise _invalid("Director run creation requires the batch transaction")
    if not generations or len(generations) != len(scenes):
        raise _invalid("An ordered generation and scene pair is required for every member")
    if any(
        not isinstance(member.input_snapshot, dict)
        or member.input_snapshot.get("provider") != "minimax_h3_director"
        for member in generations
    ):
        raise AppError(
            "DIRECTOR_AGGREGATE_REQUIRED",
            "Native continuity requires Director members; legacy and Director jobs cannot be mixed",
            422,
        )
    if (
        len({member.id for member in generations}) != len(generations)
        or len({scene.id for scene in scenes}) != len(scenes)
        or any(not member.id or member.created_by != user_id for member in generations)
    ):
        raise _invalid("Run members must be distinct and belong to the requesting user")
    workflow = await session.get(WorkflowRecord, generations[0].workflow_id, with_for_update=True)
    if workflow is None or not workflow.enabled:
        raise AppError("WORKFLOW_NOT_QUALIFIED", "Aggregate workflow is missing or disabled", 409)
    if workflow.execution_scope != "aggregate":
        raise _invalid("Director runs require an aggregate workflow execution scope")
    exporters = _segment_exporters(workflow.profile, len(generations))
    if any(
        binding.get("coverage") != "all_members"
        for binding in exporters
        if binding.get("role") == "segment"
    ):
        raise AppError(
            "DIRECTOR_AGGREGATE_NOT_QUALIFIED",
            "New Director runs require dynamic all_members native segment coverage",
            409,
        )
    if any(binding.get("node_id") not in workflow.workflow for binding in exporters):
        raise AppError(
            "DIRECTOR_AGGREGATE_NOT_QUALIFIED",
            "Aggregate exporters must identify nodes in the qualified workflow graph",
            409,
        )
    existing = await session.scalar(
        select(DirectorRunMember.scene_generation_id)
        .where(DirectorRunMember.scene_generation_id.in_([member.id for member in generations]))
        .limit(1)
    )
    if existing:
        raise AppError(
            "DIRECTOR_MEMBER_ALREADY_ASSIGNED", "Generation already belongs to a run", 409
        )

    frozen = [
        _freeze_member(member, scene, video, workflow)
        for member, scene in zip(generations, scenes, strict=True)
    ]
    first = frozen[0][2]
    if any(binding.get("transport") == "studio_native_segments_v1" for binding in exporters):
        if any(len(intent.timeline) != 1 for _, intent, _ in frozen):
            raise _invalid(
                "Native aggregate exporters require one timeline segment per scene member"
            )
    identity = _compatible_settings(first)
    if any(_compatible_settings(spec) != identity for _, _, spec in frozen[1:]):
        raise _invalid("Aggregate members require identical provider, workflow, task and settings")
    if first.seed_policy == "FIXED" and any(spec.seed != first.seed for _, _, spec in frozen):
        raise _invalid("A single aggregate execution cannot use conflicting fixed seeds")

    run_id = str(uuid4())
    assets: list[GenerationAssetBinding] = []
    timeline: list[GenerationTimelineSegment] = []
    ordinals: dict[str, int] = {}
    asset_identities: dict[str, dict[str, Any]] = {}
    continuities: list[str] = []
    members: list[dict[str, Any]] = []
    shared_source: dict[str, Any] | None = None
    cursor = 0
    for index, (generation, scene, (snapshot, intent, spec)) in enumerate(
        zip(generations, scenes, frozen, strict=True)
    ):
        continuity = (scene.spec or {}).get("continuity", "CUT")
        if continuity not in {"CUT", "CONTINUOUS"}:
            raise _invalid("Scene continuity must be CUT or CONTINUOUS")
        continuities.append(continuity)
        remap: dict[str, dict[int, int]] = {}
        sources = [asset for asset in intent.assets if asset.role == "SOURCE_VIDEO"]
        if spec.task in {"v2v", "rv2v"} and len(sources) != 1:
            raise _invalid("Video aggregate members require exactly one source video")
        for asset in sorted(intent.assets, key=lambda binding: (binding.role, binding.order_index)):
            if not re.fullmatch(r"[a-f0-9]{64}", asset.checksum) or not asset.object_key:
                raise _invalid("Every aggregate asset requires a frozen checksum and object key")
            source_identity = asset.model_dump(
                mode="json", exclude={"role", "order_index", "filename"}
            )
            previous = asset_identities.setdefault(asset.id, source_identity)
            if previous != source_identity:
                raise _invalid("Shared asset identity changed between member snapshots")
            role_map = remap.setdefault(asset.role, {})
            if asset.order_index in role_map:
                raise _invalid("Member asset ordinals must be unique per role")
            if asset.role == "SOURCE_VIDEO":
                if shared_source is not None:
                    if source_identity != shared_source:
                        raise _invalid("Aggregate video members must share the same source binding")
                    role_map[asset.order_index] = 0
                    continue
                shared_source = source_identity
            ordinal = ordinals.get(asset.role, 0)
            ordinals[asset.role] = ordinal + 1
            role_map[asset.order_index] = ordinal
            suffix = Path(asset.filename).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
                raise _invalid("Aggregate asset filename needs a safe media extension")
            values = asset.model_dump(mode="json")
            values.update(
                order_index=ordinal,
                filename=(f"{run_id}_{asset.role.lower()}_{ordinal}_{asset.checksum[:12]}{suffix}"),
            )
            assets.append(GenerationAssetBinding.model_validate(values))
        local_cursor = 0
        for segment_index, segment in enumerate(intent.timeline):
            if segment.scene_id not in (None, scene.id) or segment.start_frame != local_cursor:
                raise _invalid("Member timeline must be contiguous and belong to its scene")
            selected = segment.asset_indices or {
                role: sorted(mapping) for role, mapping in remap.items()
            }
            indices = {}
            for role, selected_ordinals in selected.items():
                mapping = remap.get(role, {})
                if len(set(selected_ordinals)) != len(selected_ordinals) or any(
                    type(ordinal) is not int or ordinal not in mapping
                    for ordinal in selected_ordinals
                ):
                    raise _invalid("Timeline asset indices must refer to unique member inputs")
                if role not in remap:
                    raise _invalid("Timeline references an absent asset role")
                indices[role] = [mapping[ordinal] for ordinal in selected_ordinals]
            timeline.append(
                GenerationTimelineSegment(
                    scene_id=scene.id,
                    prompt=segment.prompt or intent.prompt,
                    start_frame=cursor + local_cursor,
                    frame_count=segment.frame_count,
                    continuity_from_previous=(
                        index > 0 and continuity == "CONTINUOUS"
                        if segment_index == 0
                        else segment.continuity_from_previous
                    ),
                    asset_indices=indices,
                )
            )
            local_cursor += segment.frame_count
        if local_cursor != intent.frames:
            raise _invalid("Member timeline must cover its frozen frame count exactly")
        members.append(
            {
                "member_index": index,
                "scene_id": scene.id,
                "scene_generation_id": generation.id,
                "continuity": continuity,
                "start_frame": cursor,
                "frame_count": intent.frames,
                "input_snapshot": snapshot,
            }
        )
        cursor += intent.frames

    motion = copy.deepcopy(first.motion_context)
    if any(segment.continuity_from_previous for segment in timeline):
        motion["enabled"] = True
    seed_resolution = {
        "requested_seed_policy": first.seed_policy,
        "resolved_global_seed": first.seed,
        "source_generation_id": generations[0].id,
        "resolution_policy": "first_member_frozen_seed",
        "segment_seed_policy": "provider_native_per_index",
        "source_repository": first.source_repository,
        "source_commit": first.source_commit,
        "members": [
            {
                "member_index": index,
                "scene_generation_id": generation.id,
                "requested_seed": spec.seed,
                "requested_seed_policy": spec.seed_policy,
                "resolved_global_seed": first.seed,
                "segment_indices": [
                    position
                    for position, segment in enumerate(timeline)
                    if segment.scene_id == generation.scene_id
                ],
            }
            for index, (generation, (_, _, spec)) in enumerate(
                zip(generations, frozen, strict=True)
            )
        ],
    }
    intent_values = frozen[0][1].model_dump(mode="json")
    intent_values.update(
        prompt="",
        prompt_provenance=PromptProvenance().model_dump(mode="json"),
        frames=cursor,
        requested_duration_seconds=sum(
            intent.requested_duration_seconds for _, intent, _ in frozen
        ),
        resolved_duration_seconds=cursor / first.fps,
        assets=[asset.model_dump(mode="json") for asset in assets],
        timeline=[segment.model_dump(mode="json") for segment in timeline],
        motion_context=motion,
    )
    try:
        aggregate_intent = GenerationIntent.model_validate(intent_values)
        spec_values = first.model_dump(mode="json", exclude={"execution_hash"})
        spec_values.update(
            aggregate_intent.model_dump(mode="json", exclude={"schema_version", "provider_task"})
        )
        spec_values.update(
            member_count=len(members),
            intent_hash=aggregate_intent.semantic_hash,
            output_prefix=f"studio/{video.id}/director-runs/{run_id}",
            provenance={
                **copy.deepcopy(first.provenance),
                "aggregate_seed_resolution": copy.deepcopy(seed_resolution),
            },
        )
        aggregate_spec = DirectorExecutionSpec.finalize(**spec_values)
    except (ValidationError, TypeError) as exc:
        raise _invalid("Aggregate execution exceeds the supported frozen contract") from exc
    evidence = require_director_aggregate_qualification(
        workflow.profile, aggregate_spec, member_count=len(members), continuities=continuities
    )
    snapshot = {
        "schema_version": 3,
        "kind": "director_aggregate",
        "provider": aggregate_spec.provider,
        "workflow_id": workflow.id,
        "workflow_code": workflow.code,
        "workflow_version": workflow.version,
        "workflow_hash": aggregate_spec.workflow_hash,
        "slot_map_hash": aggregate_spec.slot_map_hash,
        "profile_hash": aggregate_spec.profile_hash,
        "base_workflow": copy.deepcopy(workflow.workflow),
        "workflow": copy.deepcopy(workflow.workflow),
        "slots": copy.deepcopy(workflow.slots),
        "required_slots": copy.deepcopy(workflow.required_slots),
        "runtime_profile": copy.deepcopy(workflow.profile),
        "source_scope": {"project_id": video.project_id, "product_id": video.product_id},
        "video_id": video.id,
        "video_revision": video.revision,
        "mode": aggregate_spec.requested_mode,
        "task": aggregate_spec.task,
        "requested_quality_profile": aggregate_spec.quality_profile,
        "requested_aspect_ratio": aggregate_spec.canvas.aspect_ratio,
        "width": aggregate_spec.canvas.width,
        "height": aggregate_spec.canvas.height,
        "resolved_width": aggregate_spec.canvas.width,
        "resolved_height": aggregate_spec.canvas.height,
        "frames": aggregate_spec.frames,
        "fps": aggregate_spec.fps,
        "duration_seconds": aggregate_spec.requested_duration_seconds,
        "resolved_duration_seconds": aggregate_spec.resolved_duration_seconds,
        "seed": aggregate_spec.seed,
        "seed_policy": aggregate_spec.seed_policy,
        "seed_source_generation_id": generations[0].id,
        "seed_resolution": copy.deepcopy(seed_resolution),
        "prompt": aggregate_spec.prompt,
        "negative_prompt": aggregate_spec.negative_prompt,
        "steps": aggregate_spec.steps,
        "cfg": aggregate_spec.cfg,
        "assets": [asset.model_dump(mode="json") for asset in aggregate_spec.assets],
        "members": members,
        "member_count": len(members),
        "continuities": continuities,
        "generation_intent": aggregate_intent.model_dump(mode="json"),
        "director_execution": aggregate_spec.model_dump(mode="json"),
        "aggregate_qualification": evidence,
        "aggregate_settings_hash": evidence["settings_hash"],
        "output_artifacts": copy.deepcopy(workflow.profile["output_artifacts"]),
        "output_node": workflow.profile.get("output_node"),
    }
    snapshot["semantic_hash"] = stable_hash(snapshot)
    run = DirectorRun(
        id=run_id,
        video_id=video.id,
        workflow_id=workflow.id,
        provider=aggregate_spec.provider,
        task=aggregate_spec.task,
        status="CREATED",
        phase="PENDING",
        input_snapshot=snapshot,
        created_by=user_id,
        request_id=request_id,
    )
    from apps.api.app.services.generation_freshness import bind_execution_group_freshness

    bind_execution_group_freshness(generations, run)
    if persist:
        await persist_prepared_director_run(session, run)
    return run


async def persist_prepared_director_run(session: AsyncSession, run: DirectorRun) -> DirectorRun:
    """Persist exactly the qualified preflight snapshot within the caller's transaction."""
    if not session.in_transaction():
        raise _invalid("Director run persistence requires the batch transaction")
    snapshot = run.input_snapshot
    unsigned = {key: value for key, value in snapshot.items() if key != "semantic_hash"}
    if snapshot.get("semantic_hash") != stable_hash(unsigned):
        raise _invalid("Prepared Director run snapshot changed after preflight")
    members = snapshot["members"]
    session.add(run)
    await session.flush()
    for member in members:
        session.add(
            DirectorRunMember(
                director_run_id=run.id,
                scene_id=member["scene_id"],
                scene_generation_id=member["scene_generation_id"],
                member_index=member["member_index"],
                continuity=member["continuity"],
                status="CREATED",
            )
        )
    await session.flush()
    return run


def require_frozen_director_run(
    snapshot: dict[str, Any], workflow: WorkflowRecord
) -> DirectorExecutionSpec:
    """Validate aggregate totals from frozen members, preserving per-scene qualification.

    A run's duration and frames are sums of already resolved member inputs. They
    must never be sent through the single-scene duration/frame resolver again.
    """
    unsigned = {key: value for key, value in snapshot.items() if key != "semantic_hash"}
    if snapshot.get("kind") != "director_aggregate" or snapshot.get("semantic_hash") != stable_hash(
        unsigned
    ):
        raise _invalid("Frozen aggregate semantic inputs changed")
    from apps.api.app.services.workflow_router import require_director_execution

    require_director_execution(workflow)
    if not workflow.enabled or workflow.execution_scope != "aggregate":
        raise _invalid("Frozen aggregate workflow is missing, disabled or outside aggregate scope")
    approved = approved_record(workflow)
    require_contract(
        workflow,
        approved,
        snapshot["requested_quality_profile"],
        snapshot["requested_aspect_ratio"],
    )
    try:
        spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
        intent = GenerationIntent.model_validate(snapshot["generation_intent"])
    except (KeyError, ValueError, TypeError) as exc:
        raise _invalid("Frozen aggregate intent/execution contract is invalid") from exc
    if (
        spec.workflow_id != workflow.id
        or spec.workflow_code != workflow.code
        or spec.workflow_version != workflow.version
        or spec.workflow_hash != approved.workflow_hash
        or spec.slot_map_hash != approved.slot_map_hash
        or spec.profile_hash != profile_hash(workflow.profile)
        or profile_hash(spec.provenance["workflow_profile"]) != spec.profile_hash
        or spec.intent_hash != intent.semantic_hash
        or spec.task != intent.provider_task
        or snapshot["base_workflow"] != workflow.workflow
        or snapshot["workflow"] != workflow.workflow
        or snapshot["slots"] != workflow.slots
        or snapshot["required_slots"] != workflow.required_slots
        or snapshot["output_artifacts"] != workflow.profile["output_artifacts"]
        or snapshot["assets"] != [asset.model_dump(mode="json") for asset in spec.assets]
    ):
        raise _invalid("Frozen aggregate differs from its qualified workflow or intent")
    intent_values = intent.model_dump(mode="json", exclude={"schema_version", "provider_task"})
    if spec.model_dump(mode="json", include=set(intent_values)) != intent_values:
        raise _invalid("Frozen aggregate execution differs from its intent")
    fields = {
        "provider": spec.provider,
        "workflow_id": spec.workflow_id,
        "workflow_hash": spec.workflow_hash,
        "slot_map_hash": spec.slot_map_hash,
        "profile_hash": spec.profile_hash,
        "task": spec.task,
        "mode": spec.requested_mode,
        "requested_quality_profile": spec.quality_profile,
        "requested_aspect_ratio": spec.canvas.aspect_ratio,
        "width": spec.canvas.width,
        "height": spec.canvas.height,
        "resolved_width": spec.canvas.width,
        "resolved_height": spec.canvas.height,
        "frames": spec.frames,
        "fps": spec.fps,
        "duration_seconds": spec.requested_duration_seconds,
        "resolved_duration_seconds": spec.resolved_duration_seconds,
        "seed": spec.seed,
        "seed_policy": spec.seed_policy,
        "steps": spec.steps,
        "cfg": spec.cfg,
        "prompt": spec.prompt,
        "negative_prompt": spec.negative_prompt,
    }
    if any(snapshot.get(key) != value for key, value in fields.items()):
        raise _invalid("Frozen aggregate execution metadata changed")
    members = snapshot["members"]
    count = snapshot["member_count"]
    if (
        type(count) is not int
        or count < 1
        or len(members) != count
        or len(spec.timeline) != count
        or len(snapshot["continuities"]) != count
        or [member.get("member_index") for member in members] != list(range(count))
        or any(type(member.get("member_index")) is not int for member in members)
        or len({member["scene_id"] for member in members}) != count
        or len({member["scene_generation_id"] for member in members}) != count
    ):
        raise _invalid("Frozen aggregate member coverage changed")
    cursor, duration = 0, 0.0
    shared = _compatible_settings(spec)
    shared.pop("provenance")
    shared.pop("motion_context")
    for index, (member, segment) in enumerate(zip(members, spec.timeline, strict=True)):
        frozen = member["input_snapshot"]
        require_frozen(frozen, workflow)
        member_intent = GenerationIntent.model_validate(frozen["generation_intent"])
        member_spec = DirectorExecutionSpec.model_validate(frozen["director_execution"])
        member_settings = _compatible_settings(member_spec)
        member_settings.pop("provenance")
        member_settings.pop("motion_context")
        expected_motion = copy.deepcopy(member_spec.motion_context)
        if any(boundary == "CONTINUOUS" for boundary in snapshot["continuities"][1:]):
            expected_motion["enabled"] = True
        if (
            member_settings != shared
            or expected_motion != spec.motion_context
            or (index == 0 or spec.seed_policy == "FIXED")
            and member_spec.seed != spec.seed
            or member["continuity"] != snapshot["continuities"][index]
            or member["start_frame"] != cursor
            or segment.start_frame != cursor
            or member["frame_count"] != member_intent.frames
            or segment.frame_count != member_intent.frames
            or segment.scene_id != member["scene_id"]
            or len(member_intent.timeline) != 1
            or member_intent.timeline[0].scene_id not in (None, member["scene_id"])
            or segment.prompt != (member_intent.timeline[0].prompt or member_intent.prompt)
            or segment.continuity_from_previous
            != (index > 0 and member["continuity"] == "CONTINUOUS")
        ):
            raise _invalid("Frozen aggregate timeline differs from resolved member inputs")
        cursor += member_intent.frames
        duration += member_intent.requested_duration_seconds
    if (
        cursor != spec.frames
        or duration != spec.requested_duration_seconds
        or spec.resolved_duration_seconds != cursor / spec.fps
    ):
        raise _invalid("Frozen aggregate totals differ from resolved member inputs")
    evidence = require_director_aggregate_qualification(
        workflow.profile, spec, member_count=count, continuities=snapshot["continuities"]
    )
    if (
        snapshot["aggregate_settings_hash"] != evidence["settings_hash"]
        or snapshot["aggregate_qualification"] != evidence
    ):
        raise _invalid("Frozen aggregate qualification changed")
    return spec
