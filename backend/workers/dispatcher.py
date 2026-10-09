"""One in-flight ComfyUI job, durable correlation, and restart reattachment."""

from __future__ import annotations

import asyncio
import copy
import logging
import shutil
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    DirectorRun,
    DirectorRunMember,
    GenerationAsset,
    GenerationAttempt,
    Scene,
    SceneGeneration,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.integrations.comfy_adapter import ComfyError, SubmissionUncertain
from apps.api.app.providers.minimax_h3_director.collector import (
    artifact_manifest,
    report_artifacts,
    validate_artifact,
)
from apps.api.app.providers.minimax_h3_director.contracts import DirectorExecutionSpec
from apps.api.app.providers.minimax_h3_director.workflow_builder import DirectorWorkflowBuilder
from apps.api.app.services.generation_freshness import (
    is_generation_fresh,
    lock_generation_dependencies,
)
from apps.api.app.services.workflow_contracts import require_frozen
from workers.common import (
    GENERATION_ACTIVE,
    GENERATION_TERMINAL,
    lease_deadline,
    lock_scheduler,
    oldest_pending_generation,
    refresh_video,
    save_output_file,
    service_loop,
    staging_directory,
    validate_generated_video_path,
    worker_id,
)

logger = logging.getLogger(__name__)
GENERATION_FLOW_RANK = {
    "CREATED": 0,
    "DISPATCHING": 1,
    "QUEUED": 2,
    "RUNNING": 3,
    "COLLECTING": 4,
}


def queue_prompt_ids(queue: dict, group: str) -> set[str]:
    result = set()
    for item in queue.get(group, []):
        if isinstance(item, (list, tuple)) and len(item) > 1:
            result.add(str(item[1]))
        elif isinstance(item, dict) and item.get("prompt_id"):
            result.add(str(item["prompt_id"]))
    return result


def history_status(history: dict) -> str:
    status = history.get("status", {})
    if status.get("status_str") == "error":
        return "FAILED"
    if status.get("completed") or status.get("status_str") == "success":
        return "COMPLETED"
    return "RUNNING"


class Dispatcher:
    job_model = SceneGeneration
    attempt_model = GenerationAttempt
    attempt_job_column = GenerationAttempt.generation_id

    def __init__(self, factory, adapter, store, ffmpeg, settings):
        self.factory = factory
        self.adapter = adapter
        self.store = store
        self.ffmpeg = ffmpeg
        self.settings = settings
        self.owner = worker_id("dispatcher")

    async def claim(self) -> str | None:
        async with self.factory() as session, session.begin():
            await lock_scheduler(session, 971031)
            aggregate_active = await session.scalar(
                select(DirectorRun.id).where(DirectorRun.status.in_(GENERATION_ACTIVE)).limit(1)
            )
            if aggregate_active:
                return None
            standalone = (
                ~select(DirectorRunMember.id)
                .where(DirectorRunMember.scene_generation_id == SceneGeneration.id)
                .exists()
            )
            # Existing external work always consumes the sole admission slot,
            # including an uncertain submission awaiting operator resolution.
            active = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.status.in_(GENERATION_ACTIVE), standalone)
                .order_by(SceneGeneration.created_at, SceneGeneration.id)
                .with_for_update()
                .limit(1)
            )
            if active is not None:
                now = utcnow()
                expiry = active.lease_expires_at
                if expiry is not None and expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=now.tzinfo)
                if expiry is not None and expiry > now:
                    return None
                active.claimed_by = self.owner
                active.lease_expires_at = lease_deadline(self.settings)
                return active.id
            kind, generation = await oldest_pending_generation(session)
            if kind != "standalone":
                return None
            generation.status = "DISPATCHING"
            generation.phase = "PREPARING"
            generation.revision += 1
            generation.claimed_by = self.owner
            generation.lease_expires_at = lease_deadline(self.settings)
            attempt = await session.scalar(
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation.id)
                .order_by(GenerationAttempt.attempt_no.desc())
                .limit(1)
            )
            if attempt is None or attempt.status in {"FAILED", "CANCELLED"}:
                if generation.attempt_count >= self.settings.max_generation_attempts:
                    generation.status = "FAILED"
                    generation.phase = "FAILED"
                    generation.error_code = "GENERATION_RETRY_EXHAUSTED"
                    generation.error_message = "Technical retry limit was reached"
                    generation.finished_at = utcnow()
                    generation.claimed_by = None
                    generation.lease_expires_at = None
                    return None
                generation.attempt_count += 1
                session.add(
                    GenerationAttempt(
                        generation_id=generation.id,
                        attempt_no=generation.attempt_count,
                        status="CREATED",
                        client_id=str(uuid4()),
                    )
                )
            else:
                generation.attempt_count = max(generation.attempt_count, attempt.attempt_no)
            return generation.id

    async def _read(self, generation_id: str):
        async with self.factory() as session:
            generation = await session.get(self.job_model, generation_id)
            attempt = await session.scalar(
                select(self.attempt_model)
                .where(type(self).attempt_job_column == generation_id)
                .order_by(self.attempt_model.attempt_no.desc())
                .limit(1)
            )
            return generation, attempt

    async def _update(self, generation_id: str, **values) -> bool:
        async with self.factory() as session, session.begin():
            generation = await session.get(self.job_model, generation_id, with_for_update=True)
            if (
                generation is None
                or generation.claimed_by != self.owner
                or generation.status in GENERATION_TERMINAL
            ):
                return False
            desired_status = values.get("status")
            if generation.status == "CANCEL_REQUESTED" and desired_status != "CANCELLED":
                values.clear()
            elif (
                desired_status in GENERATION_FLOW_RANK
                and generation.status in GENERATION_FLOW_RANK
                and GENERATION_FLOW_RANK[desired_status] < GENERATION_FLOW_RANK[generation.status]
            ):
                values.clear()
            changed = False
            for key, value in values.items():
                if key == "started_at" and generation.started_at is not None:
                    continue
                if getattr(generation, key) != value:
                    setattr(generation, key, value)
                    changed = True
            generation.lease_expires_at = lease_deadline(self.settings)
            if not changed:
                return True
            generation.revision += 1
            generation.progress_updated_at = utcnow()
            attempt = await session.scalar(
                select(self.attempt_model)
                .where(type(self).attempt_job_column == generation_id)
                .order_by(self.attempt_model.attempt_no.desc())
                .limit(1)
            )
            if (
                attempt
                and "status" in values
                and values.get("status")
                in {
                    "RUNNING",
                    "COLLECTING",
                }
            ):
                attempt.status = values["status"]
            await self._project_members(session, generation, values)
            return True

    async def _project_members(self, session, generation, values):
        """Standalone jobs have no aggregate members to project."""

    async def _hold(self, generation_id: str, code: str, message: str) -> None:
        async with self.factory() as session, session.begin():
            generation = await session.get(self.job_model, generation_id, with_for_update=True)
            if (
                generation
                and generation.claimed_by == self.owner
                and generation.status not in GENERATION_TERMINAL
            ):
                generation.error_code = code
                generation.error_message = message[:2000]
                generation.phase = "RECONCILING"
                generation.progress_updated_at = utcnow()
                generation.revision += 1
                # Backoff protects ComfyUI when history retention has removed a job.
                generation.lease_expires_at = utcnow() + timedelta(
                    seconds=max(15, self.settings.worker_poll_seconds * 5)
                )
                await self._project_members(session, generation, {})

    async def _finish(
        self,
        generation_id: str,
        status: str,
        code=None,
        message=None,
        asset_id=None,
        artifacts=None,
    ):
        async with self.factory() as session, session.begin():
            generation_ref = (
                await session.execute(
                    select(SceneGeneration.video_id, SceneGeneration.scene_id).where(
                        SceneGeneration.id == generation_id
                    )
                )
            ).one_or_none()
            if generation_ref is None:
                return
            video_id, scene_id = generation_ref
            video = await session.scalar(
                select(Video)
                .where(Video.id == video_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if video is None:
                return
            await lock_generation_dependencies(session, video)
            scene = await session.scalar(
                select(Scene)
                .where(Scene.id == scene_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            generation = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.id == generation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                generation is None
                or generation.claimed_by != self.owner
                or generation.status in GENERATION_TERMINAL
            ):
                return
            if generation.status == "CANCEL_REQUESTED":
                status, asset_id = "CANCELLED", None
            if status == "COMPLETED" and asset_id:
                output_asset = await session.scalar(
                    select(Asset)
                    .where(Asset.id == asset_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if output_asset is None or output_asset.status != "READY":
                    status = "FAILED"
                    code = "OUTPUT_ASSET_NOT_READY"
                    message = "Generated output was deleted or not committed"
                    asset_id = None
                else:
                    measured = copy.deepcopy(output_asset.media_metadata or {})
                    measured["size_bytes"] = output_asset.size_bytes
                    measured["checksum"] = output_asset.checksum
                    frozen = generation.input_snapshot or {}
                    director = frozen.get("director_execution") or {}
                    measured_duration = measured.get(
                        "video_duration_seconds", measured.get("duration_seconds")
                    )
                    generation.output_metadata = {
                        **measured,
                        "requested": {
                            "canvas": {
                                "width": frozen.get("width"),
                                "height": frozen.get("height"),
                                "aspect_ratio": frozen.get("requested_aspect_ratio"),
                            },
                            "fps": frozen.get("fps", frozen.get("runtime_profile", {}).get("fps")),
                            "duration_seconds": frozen.get("duration_seconds"),
                            "frames": frozen.get("frames"),
                            "quality_profile": frozen.get("requested_quality_profile"),
                            "task": director.get("task", frozen.get("mode")),
                        },
                        "resolved": {
                            "width": frozen.get("resolved_width", frozen.get("width")),
                            "height": frozen.get("resolved_height", frozen.get("height")),
                            "duration_seconds": frozen.get("resolved_duration_seconds"),
                            "frames": frozen.get("frames"),
                        },
                        "measured": {
                            "width": measured.get("width"),
                            "height": measured.get("height"),
                            "fps": measured.get("fps"),
                            "duration_seconds": measured_duration,
                            "frames": measured.get("frame_count", measured.get("frames")),
                            "has_audio": measured.get("has_audio"),
                            "video_codec": measured.get("video_codec", measured.get("codec")),
                            "audio_codec": measured.get("audio_codec"),
                            "audio_sample_rate": measured.get("audio_sample_rate"),
                            "audio_channels": measured.get("audio_channels"),
                        },
                        "provider": {
                            "name": director.get("provider", frozen.get("provider", "legacy_h3")),
                            "version": director.get("provider_version"),
                            "source_commit": director.get("source_commit"),
                        },
                        "workflow": {
                            "id": frozen.get("workflow_id"),
                            "code": frozen.get("workflow_code"),
                            "version": frozen.get("workflow_version"),
                            "hash": frozen.get("workflow_hash"),
                        },
                    }
                    if artifacts is not None:
                        generation.output_metadata["artifacts"] = artifacts
            generation.status = status
            generation.phase = status
            generation.output_asset_id = asset_id
            generation.error_code = code
            generation.error_message = str(message)[:2000] if message else None
            generation.finished_at = utcnow()
            generation.progress_updated_at = generation.finished_at
            generation.lease_expires_at = None
            generation.claimed_by = None
            generation.revision += 1
            attempt = await session.scalar(
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation_id)
                .order_by(GenerationAttempt.attempt_no.desc())
                .limit(1)
            )
            if attempt:
                attempt.status = status
                attempt.finished_at = generation.finished_at
                attempt.error_code = code
                attempt.error_message = generation.error_message
            if status == "COMPLETED" and generation.operation == "ORIGINAL":
                if (
                    scene
                    and scene.enabled
                    and scene.selected_generation_id is None
                    and scene.revision == generation.input_snapshot.get("scene_revision")
                    and await is_generation_fresh(session, scene, video, generation)
                ):
                    scene.selected_generation_id = generation.id
                    scene.revision += 1
                    video.revision += 1
            await refresh_video(session, generation.video_id)

    async def _retry_or_finish(self, generation_id: str, code: str, message: str) -> None:
        async with self.factory() as session, session.begin():
            video_id = await session.scalar(
                select(SceneGeneration.video_id).where(SceneGeneration.id == generation_id)
            )
            if video_id is None:
                return
            video = await session.scalar(
                select(Video)
                .where(Video.id == video_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            generation = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.id == generation_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                generation is None
                or video is None
                or generation.claimed_by != self.owner
                or generation.status in GENERATION_TERMINAL
            ):
                return
            attempt = await session.scalar(
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation_id)
                .order_by(GenerationAttempt.attempt_no.desc())
                .limit(1)
            )
            if attempt:
                cancelled = generation.status == "CANCEL_REQUESTED"
                attempt.status = "CANCELLED" if cancelled else "FAILED"
                attempt.error_code = None if cancelled else code
                attempt.error_message = None if cancelled else message[:2000]
                attempt.finished_at = utcnow()
            if generation.status == "CANCEL_REQUESTED":
                generation.status = "CANCELLED"
                generation.phase = "CANCELLED"
                generation.finished_at = utcnow()
                generation.claimed_by = None
                generation.lease_expires_at = None
            elif generation.attempt_count < self.settings.max_generation_attempts:
                generation.status = "CREATED"
                generation.phase = "RETRY_PENDING"
                generation.comfy_prompt_id = None
                generation.error_code = code
                generation.error_message = message[:2000]
                generation.claimed_by = None
                generation.lease_expires_at = None
            else:
                generation.status = "FAILED"
                generation.phase = "FAILED"
                generation.error_code = code
                generation.error_message = message[:2000]
                generation.finished_at = utcnow()
                generation.claimed_by = None
                generation.lease_expires_at = None
            generation.progress_updated_at = utcnow()
            generation.revision += 1
            await refresh_video(session, video.id)

    async def _progress_listener(self, generation_id: str, client_id: str, prompt_id: str) -> None:
        try:
            async for event in self.adapter.stream_progress(client_id, prompt_id):
                if event.get("type") == "progress":
                    await self._update(
                        generation_id,
                        status="RUNNING",
                        phase="GENERATING",
                        progress_current=int(event["current"]),
                        progress_total=int(event["total"]),
                        started_at=utcnow(),
                    )
                elif event.get("type") == "execution_start":
                    await self._update(
                        generation_id,
                        status="RUNNING",
                        phase="GENERATING",
                        started_at=utcnow(),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "comfy_progress_stream_unavailable",
                exc_info=True,
                extra={"generation_id": generation_id},
            )

    def _validate_frozen_snapshot(self, snapshot: dict, record: WorkflowRecord):
        return require_frozen(snapshot, record)

    async def _require_execution_snapshot(self, snapshot: dict) -> None:
        """Validate execution authority without staging assets or contacting ComfyUI."""
        from apps.api.app.services.workflow_router import require_director_execution

        async with self.factory() as session:
            record = await session.get(WorkflowRecord, snapshot.get("workflow_id"))
            if record is None or not record.enabled:
                raise AppError(
                    "WORKFLOW_NOT_QUALIFIED", "Frozen workflow is missing or disabled", 409
                )
            require_director_execution(record)
            self._validate_frozen_snapshot(snapshot, record)

    async def _prepare_workflow(self, snapshot: dict) -> dict:
        await self._require_execution_snapshot(snapshot)
        expected_sizes: dict[str, int] = {}
        async with self.factory() as session:
            for frozen_asset in snapshot.get("assets", []):
                asset = await session.get(Asset, frozen_asset["id"])
                if (
                    asset is None
                    or asset.status != "READY"
                    or asset.deleted_at is not None
                    or asset.object_key != frozen_asset["object_key"]
                    or not frozen_asset.get("checksum")
                    or asset.checksum != frozen_asset["checksum"]
                ):
                    raise AppError(
                        "ASSET_INTEGRITY_FAILED", "Frozen reference is unavailable or changed", 409
                    )
                frozen_size = frozen_asset.get("size_bytes")
                if frozen_size is not None and frozen_size != asset.size_bytes:
                    raise AppError("ASSET_INTEGRITY_FAILED", "Frozen reference size changed", 409)
                if type(asset.size_bytes) is not int or asset.size_bytes <= 0:
                    raise AppError(
                        "ASSET_INTEGRITY_FAILED",
                        "Frozen reference size is unavailable",
                        409,
                    )
                expected_sizes[str(asset.id)] = asset.size_bytes
        if snapshot.get("director_execution"):
            spec = DirectorExecutionSpec.model_validate(snapshot["director_execution"])
            staged: dict[tuple[str, int], str] = {}
            max_upload_bytes = int(self.settings.max_upload_bytes)
            if max_upload_bytes < 1:
                raise ValueError("INVALID_REFERENCE_SIZE_LIMIT")
            for asset in sorted(
                snapshot.get("assets", []),
                key=lambda item: (item.get("role", ""), item.get("order_index", 0)),
            ):
                expected_size = expected_sizes[str(asset["id"])]
                if expected_size > max_upload_bytes:
                    raise ValueError("ASSET_SIZE_MISMATCH")
                extension = asset["filename"].rsplit(".", 1)[-1]
                staged_name = (
                    f"{spec.execution_hash[:24]}_{asset['role'].lower()}_{asset['order_index']}_"
                    f"{asset['checksum'][:12]}.{extension}"
                )
                async with staging_directory(self.settings, "reference-") as directory:
                    usage = await asyncio.to_thread(shutil.disk_usage, directory)
                    if usage.free - expected_size < self.settings.min_free_disk_bytes:
                        raise RuntimeError("INSUFFICIENT_DISK_SPACE")
                    path = directory / staged_name
                    downloaded = await self.store.download_to_path(
                        asset["object_key"], path, max_bytes=expected_size
                    )
                    if downloaded["size"] != expected_size:
                        raise ValueError("ASSET_SIZE_MISMATCH")
                    if downloaded["checksum"] != asset["checksum"]:
                        raise ValueError("ASSET_CHECKSUM_MISMATCH")
                    uploaded = await self.adapter.upload_file(
                        path, staged_name, asset["content_type"]
                    )
                    # Keep the staging file until the multipart request releases
                    # its file handle; the context also removes it on failure.
                    path.unlink(missing_ok=True)
                if isinstance(uploaded, dict):
                    uploaded = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
                staged[(str(asset["role"]), int(asset.get("order_index", 0)))] = str(uploaded)
            object_info = await self.adapter.object_info()
            return DirectorWorkflowBuilder().build(
                base_workflow=snapshot["base_workflow"],
                spec=spec,
                staged_assets=staged,
                object_info=object_info,
            )

        raise AppError("WORKFLOW_RETIRED", "Director execution contract is required", 409)

    async def _submit_or_recover(self, generation, attempt) -> str | None:
        try:
            await self._require_execution_snapshot(generation.input_snapshot)
        except AppError as exc:
            if generation.comfy_prompt_id or (attempt and attempt.status != "CREATED"):
                # Keep external correlation and admission while an operator resolves
                # the historical job. Never resume collection or submit another graph.
                await self._hold(generation.id, exc.code, exc.message)
                return None
            raise
        if generation.comfy_prompt_id:
            return generation.comfy_prompt_id
        if attempt is None:
            await self._hold(
                generation.id, "ATTEMPT_MISSING", "No durable submission correlation exists"
            )
            return None
        if attempt.status != "CREATED":
            prompt_id = await self.adapter.find_by_client_id(attempt.client_id)
            if not prompt_id:
                await self._hold(
                    generation.id,
                    "SUBMISSION_UNCERTAIN",
                    "Submission may have reached ComfyUI. Retained admission slot; "
                    "inspect queue/history before retrying.",
                )
                return None
        else:
            if generation.status == "CANCEL_REQUESTED":
                await self._finish(generation.id, "CANCELLED")
                return None
            workflow = await self._prepare_workflow(generation.input_snapshot)
            async with self.factory() as session, session.begin():
                current = await session.get(self.job_model, generation.id, with_for_update=True)
                if current.claimed_by != self.owner or current.status != "DISPATCHING":
                    return None
                durable_attempt = await session.get(self.attempt_model, attempt.id)
                durable_attempt.status = "DISPATCHING"
                current.phase = "SUBMITTING"
                current.lease_expires_at = lease_deadline(self.settings)
            try:
                prompt_id = await self.adapter.submit(workflow, attempt.client_id)
            except SubmissionUncertain as exc:
                await self._hold(generation.id, "SUBMISSION_UNCERTAIN", str(exc))
                return None
        async with self.factory() as session, session.begin():
            current = await session.get(self.job_model, generation.id, with_for_update=True)
            durable_attempt = await session.get(self.attempt_model, attempt.id)
            # Preserve the external ID even if cancellation happened during submit.
            durable_attempt.comfy_prompt_id = prompt_id
            durable_attempt.status = "QUEUED"
            current.comfy_prompt_id = prompt_id
            if current.claimed_by != self.owner or current.status in GENERATION_TERMINAL:
                return None
            if current.status != "CANCEL_REQUESTED":
                current.status = "QUEUED"
                current.phase = "QUEUED"
                current.queued_at = current.queued_at or utcnow()
            current.error_code = None
            current.error_message = None
            current.progress_updated_at = utcnow()
            current.revision += 1
            await self._project_members(session, current, {"status": current.status})
        return prompt_id

    async def _collect(self, generation, history: dict):
        await self._require_execution_snapshot(generation.input_snapshot)
        if not await self._update(generation.id, status="COLLECTING", phase="COLLECTING"):
            return
        await self._collect_director(generation, history)

    async def _collect_director(self, generation, history: dict):
        spec = DirectorExecutionSpec.model_validate(generation.input_snapshot["director_execution"])
        artifacts = artifact_manifest(history, spec.provenance["workflow_profile"])
        async with self.factory() as session:
            video = await session.get(Video, generation.video_id)
        if video is None:
            raise ValueError("DIRECTOR_VIDEO_MISSING")
        manifest, final_asset_id = [], None
        for artifact in artifacts:
            if not await self._update(generation.id):
                return
            max_bytes = getattr(self.settings, "max_generated_output_bytes", 500 * 1024**2)
            async with staging_directory(self.settings, f"director-{generation.id}-") as directory:
                path = directory / "output.mp4"
                actual = await self.adapter.download_to_path(artifact.locator, path, max_bytes)
                metadata = await self.ffmpeg.probe(path)
                metadata = await validate_generated_video_path(
                    path,
                    comfy_kind=artifact.locator["kind"],
                    metadata=metadata,
                )
                expected = artifact.expected
                if artifact.role == "final" and not spec.refine.get("enabled"):
                    frozen_expected = {
                        "width": spec.canvas.width,
                        "height": spec.canvas.height,
                        "fps": spec.fps,
                        "frames": spec.frames,
                        "has_audio": spec.audio_policy.get("mode", "generate") != "mute",
                    }
                    if any(
                        key in expected and expected[key] != value
                        for key, value in frozen_expected.items()
                    ):
                        raise ValueError("DIRECTOR_ARTIFACT_EXPECTATIONS_CONFLICT_WITH_EXECUTION")
                    expected = {**expected, **frozen_expected}
                elif artifact.role == "final":
                    if not {"width", "height", "fps", "frames", "has_audio"} <= set(expected):
                        raise ValueError("DIRECTOR_REFINED_OUTPUT_CONTRACT_REQUIRED")
                    if expected["fps"] != spec.fps or expected["frames"] != spec.frames:
                        raise ValueError("DIRECTOR_REFINED_OUTPUT_CONTRACT_CONFLICT")
                validate_artifact(metadata, expected)
                owner_id = (
                    generation.id
                    if artifact.role == "final"
                    else str(
                        uuid5(
                            NAMESPACE_URL,
                            f"director:{generation.id}:{artifact.role}:{artifact.member_index}:{artifact.ordinal}",
                        )
                    )
                )
                asset_id = await save_output_file(
                    self.factory,
                    self.store,
                    owner_id=owner_id,
                    role="GENERATED_VIDEO",
                    project_id=video.project_id,
                    created_by=generation.created_by,
                    path=path,
                    expected_checksum=actual["checksum"],
                    expected_size=actual["size"],
                    metadata=metadata,
                    max_bytes=getattr(self.settings, "max_generated_output_bytes", 500 * 1024**2),
                    claim_timeout_seconds=getattr(
                        self.settings, "asset_operation_claim_timeout_seconds", 900
                    ),
                )
            if artifact.role == "final":
                final_asset_id = asset_id
            # Preserve lifecycle reachability for every accepted comparison/segment
            # artifact, including collection that is interrupted and reconciled.
            async with self.factory() as session, session.begin():
                current = await session.get(SceneGeneration, generation.id, with_for_update=True)
                if current is None or current.claimed_by != self.owner:
                    return
                existing = await session.scalar(
                    select(GenerationAsset).where(
                        GenerationAsset.generation_id == generation.id,
                        GenerationAsset.role
                        == f"DIRECTOR_{artifact.role.upper().replace('-', '_')}",
                        GenerationAsset.order_index == len(manifest),
                    )
                )
                if existing is None:
                    session.add(
                        GenerationAsset(
                            generation_id=generation.id,
                            asset_id=asset_id,
                            role=f"DIRECTOR_{artifact.role.upper().replace('-', '_')}",
                            order_index=len(manifest),
                        )
                    )
                elif existing.asset_id != asset_id:
                    raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")
            manifest.append(
                {
                    "role": artifact.role,
                    "ordinal": artifact.ordinal,
                    "member_index": artifact.member_index,
                    "asset_id": asset_id,
                    "provider_locator": artifact.locator,
                    "checksum": actual["checksum"],
                    "size_bytes": actual["size"],
                    "measured": metadata,
                    "execution_hash": spec.execution_hash,
                    "prompt_id": generation.comfy_prompt_id,
                    "attempt_no": generation.attempt_count,
                }
            )
        manifest.extend(report_artifacts(history, spec.provenance["workflow_profile"]))
        await self._finish(generation.id, "COMPLETED", asset_id=final_asset_id, artifacts=manifest)

    async def process(self, generation_id: str):
        generation, attempt = await self._read(generation_id)
        prompt_id = await self._submit_or_recover(generation, attempt)
        if not prompt_id:
            return
        listener = asyncio.create_task(
            self._progress_listener(generation_id, attempt.client_id, prompt_id)
        )
        started = asyncio.get_running_loop().time()
        try:
            while True:
                generation, _ = await self._read(generation_id)
                if generation.claimed_by != self.owner or generation.status in GENERATION_TERMINAL:
                    return
                if not await self._update(generation_id):
                    return
                history = await self.adapter.get_history(prompt_id)
                if history:
                    outcome = history_status(history)
                    if outcome in {"FAILED", "COMPLETED"}:
                        if generation.status == "CANCEL_REQUESTED":
                            await self._finish(generation_id, "CANCELLED")
                        elif outcome == "FAILED":
                            await self._retry_or_finish(
                                generation_id,
                                "COMFY_EXECUTION_FAILED",
                                str(history.get("status")),
                            )
                        else:
                            await self._collect(generation, history)
                        return
                queue = await self.adapter.get_queue()
                pending = queue_prompt_ids(queue, "queue_pending")
                running = queue_prompt_ids(queue, "queue_running")
                if generation.status == "CANCEL_REQUESTED":
                    if prompt_id in pending:
                        await self.adapter.delete_pending(prompt_id)
                    elif prompt_id in running:
                        await self.adapter.interrupt(prompt_id)
                    else:
                        await self._finish(generation_id, "CANCELLED")
                        return
                elif prompt_id in running:
                    await self._update(
                        generation_id,
                        status="RUNNING",
                        phase="GENERATING",
                        started_at=generation.started_at or utcnow(),
                    )
                elif prompt_id not in pending:
                    # Queue can disappear just before history becomes visible; never
                    # infer failure or resubmit from that one observation.
                    await self._hold(
                        generation_id,
                        "EXTERNAL_JOB_MISSING",
                        "Waiting for ComfyUI queue/history reconciliation",
                    )
                    return
                if (
                    asyncio.get_running_loop().time() - started
                    > self.settings.comfy_timeout_seconds
                ):
                    await self._hold(
                        generation_id,
                        "GENERATION_TIMEOUT",
                        "Job remains externally owned; reconciliation will continue",
                    )
                    return
                await asyncio.sleep(max(0.1, self.settings.worker_poll_seconds))
        finally:
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass

    async def _heartbeat(self, generation_id: str):
        while True:
            await asyncio.sleep(max(1, self.settings.lease_seconds / 3))
            if not await self._update(generation_id):
                return

    async def run_once(self) -> bool:
        generation_id = await self.claim()
        if generation_id is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(generation_id))
        try:
            await self.process(generation_id)
        except ComfyError as exc:
            generation, attempt = await self._read(generation_id)
            if generation.comfy_prompt_id or (attempt and attempt.status != "CREATED"):
                # Known validation rejection is not ambiguous; transport failures
                # after acceptance must remain recoverable.
                if getattr(exc, "code", "") in {
                    "COMFY_REJECTED",
                    "COMFY_VALIDATION_ERROR",
                    "COMFY_PROMPT_REJECTED",
                    "COMFY_OUTPUT_TOO_LARGE",
                }:
                    await self._finish(generation_id, "FAILED", exc.code, str(exc))
                else:
                    await self._hold(generation_id, "COMFY_UNAVAILABLE", str(exc))
            else:
                await self._finish(
                    generation_id, "FAILED", getattr(exc, "code", "COMFY_ERROR"), str(exc)
                )
        except (ValueError, KeyError) as exc:
            await self._finish(generation_id, "FAILED", "GENERATION_INPUT_INVALID", str(exc))
        except AppError as exc:
            await self._finish(generation_id, "FAILED", exc.code, exc.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("generation_processing_failed", extra={"generation_id": generation_id})
            await self._hold(generation_id, "WORKER_ERROR", str(exc))
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True


async def main():
    from apps.api.app.core.config import get_settings
    from apps.api.app.db.session import SessionFactory
    from apps.api.app.integrations.comfy_adapter import ComfyAdapter
    from apps.api.app.integrations.ffmpeg import FFmpeg
    from apps.api.app.integrations.minio import AssetStore
    from workers.director_dispatcher import GenerationScheduler

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    adapter = ComfyAdapter(settings)
    try:
        await service_loop(
            GenerationScheduler(
                SessionFactory, adapter, AssetStore(settings), FFmpeg(settings), settings
            ),
            settings.worker_poll_seconds,
        )
    finally:
        await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())
