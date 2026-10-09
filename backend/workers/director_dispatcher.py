"""Aggregate Director execution under the same admission lock as standalone jobs."""

from __future__ import annotations

import asyncio
import copy
import logging
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import case, literal, select, union_all

from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunAttempt,
    DirectorRunMember,
    GenerationAsset,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.providers.minimax_h3_director.collector import (
    artifact_manifest,
    report_artifacts,
    validate_artifact,
)
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.services.director_run_service import require_frozen_director_run
from apps.api.app.services.generation_freshness import (
    is_generation_fresh,
    lock_generation_dependencies,
)
from workers.common import (
    GENERATION_ACTIVE,
    GENERATION_TERMINAL,
    lease_deadline,
    lock_scheduler,
    oldest_pending_generation,
    refresh_video,
    save_output_file,
    staging_directory,
    validate_generated_video_path,
)
from workers.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


class DirectorDispatcher(Dispatcher):
    job_model = DirectorRun
    attempt_model = DirectorRunAttempt
    attempt_job_column = DirectorRunAttempt.director_run_id

    async def _members(self, session, run_id):
        return list(
            (
                await session.scalars(
                    select(DirectorRunMember)
                    .where(DirectorRunMember.director_run_id == run_id)
                    .order_by(DirectorRunMember.member_index)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def _project_members(self, session, run, values):
        for member in await self._members(session, run.id):
            generation = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.id == member.scene_generation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if generation is None or generation.status in GENERATION_TERMINAL:
                continue
            if generation.status == "CANCEL_REQUESTED":
                continue
            status = values.get("status")
            generation.phase = run.phase
            generation.error_code = run.error_code
            generation.error_message = run.error_message
            if status:
                generation.status = status
                generation.phase = run.phase
                member.status = "RUNNING" if status in {"RUNNING", "COLLECTING"} else "CREATED"
            generation.claimed_by = run.claimed_by
            generation.lease_expires_at = run.lease_expires_at
            generation.attempt_count = run.attempt_count
            for name in ("progress_current", "progress_total"):
                if name in values:
                    setattr(generation, name, values[name])
            generation.started_at = generation.started_at or run.started_at
            generation.queued_at = generation.queued_at or run.queued_at
            generation.progress_updated_at = utcnow()
            generation.revision += 1

    async def claim(self):
        async with self.factory() as session, session.begin():
            await lock_scheduler(session, 971031)
            standalone = (
                ~select(DirectorRunMember.id)
                .where(DirectorRunMember.scene_generation_id == SceneGeneration.id)
                .exists()
            )
            if await session.scalar(
                select(SceneGeneration.id)
                .where(SceneGeneration.status.in_(GENERATION_ACTIVE), standalone)
                .limit(1)
            ):
                return None
            run = await session.scalar(
                select(DirectorRun)
                .where(DirectorRun.status.in_(GENERATION_ACTIVE))
                .order_by(DirectorRun.created_at, DirectorRun.id)
                .with_for_update()
                .limit(1)
            )
            if run is not None:
                now = utcnow()
                expiry = run.lease_expires_at
                if expiry and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=now.tzinfo)
                if expiry and expiry > now:
                    return None
            else:
                kind, run = await oldest_pending_generation(session)
                if kind != "director":
                    return None
                run.status, run.phase = "DISPATCHING", "PREPARING"
            run.claimed_by = self.owner
            run.lease_expires_at = lease_deadline(self.settings)
            run.revision += 1
            attempt = await session.scalar(
                select(DirectorRunAttempt)
                .where(DirectorRunAttempt.director_run_id == run.id)
                .order_by(DirectorRunAttempt.attempt_no.desc())
                .limit(1)
            )
            if attempt is None or attempt.status in {"FAILED", "CANCELLED"}:
                if run.attempt_count >= self.settings.max_generation_attempts:
                    # Finish outside this transaction using the normal terminal path.
                    run.error_code = "GENERATION_RETRY_EXHAUSTED"
                else:
                    run.attempt_count += 1
                    session.add(
                        DirectorRunAttempt(
                            director_run_id=run.id,
                            attempt_no=run.attempt_count,
                            status="CREATED",
                            client_id=str(uuid4()),
                        )
                    )
            await self._project_members(session, run, {"status": run.status})
            return run.id

    def _validate_frozen_snapshot(self, snapshot: dict, record: WorkflowRecord):
        return require_frozen_director_run(snapshot, record)

    async def _progress_listener(self, generation_id, client_id, prompt_id):
        # The provider stream reports whole-run progress; forward it to each member.
        try:
            async for event in self.adapter.stream_progress(client_id, prompt_id):
                if event.get("type") not in {"progress", "execution_start"}:
                    continue
                run, _ = await self._read(generation_id)
                if not await self._update(
                    generation_id,
                    status="RUNNING",
                    phase="GENERATING",
                    started_at=run.started_at or utcnow(),
                ):
                    continue
                if event.get("type") == "progress":
                    total = max(0, int(event["total"]))
                    current = min(total, max(0, int(event["current"])))
                    async with self.factory() as session, session.begin():
                        run = await session.get(DirectorRun, generation_id, with_for_update=True)
                        if run.claimed_by != self.owner or run.status != "RUNNING":
                            continue
                        run.progress_updated_at = utcnow()
                        await self._project_members(
                            session,
                            run,
                            {
                                "progress_current": current,
                                "progress_total": total,
                            },
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "director_progress_stream_unavailable",
                exc_info=True,
                extra={"director_run_id": generation_id},
            )

    async def process(self, generation_id):
        run, _ = await self._read(generation_id)
        if run.error_code == "GENERATION_RETRY_EXHAUSTED":
            await self._finish(run.id, "FAILED", run.error_code, "Technical retry limit reached")
            return
        await super().process(generation_id)

    async def _retry_or_finish(self, run_id, code, message):
        async with self.factory() as session, session.begin():
            run = await session.get(DirectorRun, run_id, with_for_update=True)
            if run is None or run.claimed_by != self.owner or run.status in GENERATION_TERMINAL:
                return
            retry = (
                run.status != "CANCEL_REQUESTED"
                and run.attempt_count < self.settings.max_generation_attempts
            )
            if retry:
                attempt = await session.scalar(
                    select(DirectorRunAttempt)
                    .where(DirectorRunAttempt.director_run_id == run.id)
                    .order_by(DirectorRunAttempt.attempt_no.desc())
                    .limit(1)
                )
                if attempt:
                    attempt.status, attempt.error_code = "FAILED", code
                    attempt.error_message, attempt.finished_at = message[:2000], utcnow()
                run.status, run.phase = "CREATED", "RETRY_PENDING"
                run.comfy_prompt_id = None
                run.claimed_by = run.lease_expires_at = None
                run.error_code, run.error_message = code, message[:2000]
                run.revision += 1
                await self._project_members(
                    session,
                    run,
                    {
                        "status": "CREATED",
                        "progress_current": 0,
                        "progress_total": 0,
                    },
                )
                return
        await self._finish(run_id, "FAILED", code, message)

    async def _collect(self, run, history):
        await self._require_execution_snapshot(run.input_snapshot)
        if not await self._update(run.id, status="COLLECTING", phase="COLLECTING"):
            return
        spec = DirectorExecutionSpec.model_validate(run.input_snapshot["director_execution"])
        profile = spec.provenance["workflow_profile"]
        members = run.input_snapshot["members"]
        member_count = run.input_snapshot["member_count"]
        if (
            type(member_count) is not int
            or member_count != len(members)
            or (spec.member_count is not None and spec.member_count != member_count)
        ):
            raise ValueError("DIRECTOR_SEGMENT_COVERAGE_INVALID")
        artifacts = artifact_manifest(
            history, profile, require_final=False, member_count=member_count
        )
        segments = [artifact for artifact in artifacts if artifact.role == "segment"]
        if sorted(artifact.member_index for artifact in segments) != list(range(len(members))):
            raise ValueError("DIRECTOR_SEGMENT_COVERAGE_INVALID")
        async with self.factory() as session:
            video = await session.get(Video, run.video_id)
        manifest = []
        for artifact in artifacts:
            if artifact.member_index is not None and artifact.member_index >= len(members):
                raise ValueError("DIRECTOR_ARTIFACT_MEMBER_INVALID")
            if not await self._update(run.id):
                return
            max_bytes = getattr(self.settings, "max_generated_output_bytes", 500 * 1024**2)
            async with staging_directory(self.settings, f"director-{run.id}-") as directory:
                path = directory / "output.mp4"
                actual = await self.adapter.download_to_path(artifact.locator, path, max_bytes)
                metadata = await self.ffmpeg.probe(path)
                metadata = await validate_generated_video_path(
                    path,
                    comfy_kind=artifact.locator["kind"],
                    metadata=metadata,
                )
                expected = dict(artifact.expected)
                if artifact.role == "segment":
                    member = members[artifact.member_index]
                    frozen = member["input_snapshot"]["director_execution"]
                    required = {
                        "fps": frozen["fps"],
                        "frames": member["frame_count"],
                        "has_audio": (
                            frozen.get("audio_policy", {}).get("mode", "generate") != "mute"
                        ),
                    }
                    if (frozen.get("refine") or {}).get("enabled"):
                        if not {"width", "height"} <= set(expected):
                            raise ValueError("DIRECTOR_REFINED_OUTPUT_CONTRACT_REQUIRED")
                    else:
                        required.update(
                            width=frozen["canvas"]["width"], height=frozen["canvas"]["height"]
                        )
                    if any(
                        key in expected and expected[key] != value
                        for key, value in required.items()
                    ):
                        raise ValueError("DIRECTOR_SEGMENT_EXPECTATIONS_CONFLICT")
                    expected.update(required)
                    owner_id = member["scene_generation_id"]
                else:
                    owner_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"director:{run.id}:{artifact.role}:{artifact.member_index}:{artifact.ordinal}",
                        )
                    )
                validate_artifact(metadata, expected)
                asset_id = await save_output_file(
                    self.factory,
                    self.store,
                    owner_id=owner_id,
                    role="GENERATED_VIDEO",
                    project_id=video.project_id,
                    created_by=run.created_by,
                    path=path,
                    expected_checksum=actual["checksum"],
                    expected_size=actual["size"],
                    metadata=metadata,
                    claim_timeout_seconds=self.settings.asset_operation_claim_timeout_seconds,
                    max_bytes=max_bytes,
                )
            # Retain every artifact even if collection stops before run finalization.
            async with self.factory() as session, session.begin():
                current = await session.get(DirectorRun, run.id, with_for_update=True)
                if current is None or current.claimed_by != self.owner:
                    return
                generation_id = (
                    members[artifact.member_index]["scene_generation_id"]
                    if artifact.member_index is not None
                    else members[0]["scene_generation_id"]
                )
                role = f"DIRECTOR_{artifact.role.upper().replace('-', '_')}"
                link = await session.scalar(
                    select(GenerationAsset).where(
                        GenerationAsset.generation_id == generation_id,
                        GenerationAsset.role == role,
                        GenerationAsset.order_index == len(manifest),
                    )
                )
                if link is None:
                    session.add(
                        GenerationAsset(
                            generation_id=generation_id,
                            asset_id=asset_id,
                            role=role,
                            order_index=len(manifest),
                        )
                    )
                elif link.asset_id != asset_id:
                    raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")
            manifest.append(
                {
                    "role": artifact.role,
                    "member_index": artifact.member_index,
                    "ordinal": artifact.ordinal,
                    "asset_id": asset_id,
                    "provider_locator": artifact.locator,
                    "checksum": actual["checksum"],
                    "size_bytes": actual["size"],
                    "measured": metadata,
                    "execution_hash": spec.execution_hash,
                    "prompt_id": run.comfy_prompt_id,
                    "attempt_no": run.attempt_count,
                }
            )
        manifest.extend(report_artifacts(history, profile))
        await self._finish(run.id, "COMPLETED", artifacts=manifest)

    async def _finish(self, run_id, status, code=None, message=None, asset_id=None, artifacts=None):
        async with self.factory() as session, session.begin():
            video_id = await session.scalar(
                select(DirectorRun.video_id).where(DirectorRun.id == run_id)
            )
            if video_id is None:
                return
            video = await session.scalar(
                select(Video)
                .where(Video.id == video_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if video is None:
                return
            await lock_generation_dependencies(session, video)
            # The video lock serializes terminal selection with API cancellation.
            run = await session.scalar(
                select(DirectorRun)
                .where(DirectorRun.id == run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None or run.claimed_by != self.owner or run.status in GENERATION_TERMINAL:
                return
            members = await self._members(session, run_id)
            if run.status == "CANCEL_REQUESTED":
                status = "CANCELLED"

            member_generations = {}
            scenes = {}
            for member in members:
                member_generations[member.id] = await session.scalar(
                    select(SceneGeneration)
                    .where(SceneGeneration.id == member.scene_generation_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if status == "COMPLETED" and member.scene_id not in scenes:
                    scenes[member.scene_id] = await session.scalar(
                        select(Scene)
                        .where(Scene.id == member.scene_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
            outputs = {
                item["member_index"]: item
                for item in artifacts or []
                if item.get("role") == "segment"
            }
            if status == "COMPLETED":
                segments = [item for item in artifacts or [] if item.get("role") == "segment"]
                if (
                    any(type(item.get("member_index")) is not int for item in segments)
                    or len(segments) != len(members)
                    or set(outputs) != {member.member_index for member in members}
                ):
                    raise ValueError("DIRECTOR_SEGMENT_COVERAGE_INVALID")
                for output in outputs.values():
                    asset = await session.scalar(
                        select(Asset)
                        .where(Asset.id == output["asset_id"])
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if asset is None or asset.status != "READY":
                        raise ValueError("OUTPUT_ASSET_NOT_READY")
            sources_current = (
                all(
                    [
                        await is_generation_fresh(
                            session,
                            scenes[member.scene_id],
                            video,
                            member_generations[member.id],
                        )
                        for member in members
                    ]
                )
                if status == "COMPLETED"
                else False
            )
            auto_select_group = sources_current and all(
                [
                    scenes[member.scene_id].enabled
                    and scenes[member.scene_id].selected_generation_id is None
                    and member_generations[member.id].operation == "ORIGINAL"
                    and scenes[member.scene_id].revision
                    == member_generations[member.id].input_snapshot.get("scene_revision")
                    for member in members
                ]
            )
            for member in members:
                generation = member_generations[member.id]
                if generation is None or generation.status in GENERATION_TERMINAL:
                    continue
                output = outputs.get(member.member_index) if status == "COMPLETED" else None
                member.status = generation.status = status
                member.output_asset_id = generation.output_asset_id = (
                    output["asset_id"] if output else None
                )
                metadata = copy.deepcopy(output) if output else {}
                member.output_metadata = metadata
                generation.output_metadata = {
                    **(output.get("measured", {}) if output else {}),
                    "director_run_id": run.id,
                    "resolved_global_seed": run.input_snapshot.get("seed"),
                    "aggregate_execution_hash": (
                        run.input_snapshot.get("director_execution") or {}
                    ).get("execution_hash"),
                    "size_bytes": output.get("size_bytes") if output else None,
                    "checksum": output.get("checksum") if output else None,
                    "artifacts": [metadata] if output else [],
                }
                generation.phase = status
                generation.error_code, generation.error_message = (
                    code,
                    str(message)[:2000] if message else None,
                )
                generation.finished_at = generation.progress_updated_at = utcnow()
                generation.claimed_by = generation.lease_expires_at = None
                generation.revision += 1
                if auto_select_group:
                    scene = scenes[member.scene_id]
                    scene.selected_generation_id = generation.id
                    scene.revision += 1
            if auto_select_group:
                video.revision += 1
            run.status = run.phase = status
            run.error_code, run.error_message = code, str(message)[:2000] if message else None
            run.output_manifest = {"artifacts": artifacts or []}
            run.finished_at = run.progress_updated_at = utcnow()
            run.claimed_by = run.lease_expires_at = None
            run.revision += 1
            attempt = await session.scalar(
                select(DirectorRunAttempt)
                .where(DirectorRunAttempt.director_run_id == run_id)
                .order_by(DirectorRunAttempt.attempt_no.desc())
                .limit(1)
            )
            if attempt:
                attempt.status, attempt.finished_at = status, run.finished_at
                attempt.error_code, attempt.error_message = code, run.error_message
            await refresh_video(session, video.id)


class GenerationScheduler:
    """One process services both durable job types; DB locking protects replicas."""

    def __init__(self, factory, adapter, store, ffmpeg, settings):
        self.director = DirectorDispatcher(factory, adapter, store, ffmpeg, settings)
        self.standalone = Dispatcher(factory, adapter, store, ffmpeg, settings)

    async def run_once(self):
        # Recovery consumes the sole slot before any new admission. Director
        # members are projections, not independently dispatchable jobs.
        standalone = (
            ~select(DirectorRunMember.id)
            .where(DirectorRunMember.scene_generation_id == SceneGeneration.id)
            .exists()
        )
        candidates = union_all(
            select(
                literal("director").label("kind"),
                case((DirectorRun.status.in_(GENERATION_ACTIVE), 0), else_=1).label("priority"),
                DirectorRun.created_at.label("created_at"),
                DirectorRun.id.label("id"),
            ).where(DirectorRun.status.in_(GENERATION_ACTIVE | {"CREATED"})),
            select(
                literal("standalone").label("kind"),
                case((SceneGeneration.status.in_(GENERATION_ACTIVE), 0), else_=1),
                SceneGeneration.created_at,
                SceneGeneration.id,
            ).where(SceneGeneration.status.in_(GENERATION_ACTIVE | {"CREATED"}), standalone),
        ).subquery()
        async with self.director.factory() as session, session.begin():
            await lock_scheduler(session, 971031)
            kind = await session.scalar(
                select(candidates.c.kind)
                .where(candidates.c.priority == 0)
                .order_by(
                    candidates.c.priority,
                    candidates.c.created_at,
                    candidates.c.id,
                    candidates.c.kind,
                )
                .limit(1)
            )
            if kind is None:
                kind, _ = await oldest_pending_generation(session)
        if kind is None:
            return False
        # This query only routes work; the selected worker's advisory lock and
        # claim transaction remain the admission authority across replicas.
        # If that worker cannot claim, poll again rather than favoring the other
        # queue through a fallback that could bypass recovery or age ordering.
        return await getattr(self, kind).run_once()
