"""One in-flight ComfyUI job, durable correlation, and restart reattachment."""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from apps.api.app.db.models import Asset, GenerationAttempt, Scene, SceneGeneration, Video, utcnow
from apps.api.app.integrations.comfy_adapter import ComfyError, SubmissionUncertain
from workers.common import (
    GENERATION_ACTIVE,
    GENERATION_TERMINAL,
    check_checksum,
    lease_deadline,
    lock_scheduler,
    refresh_video,
    save_output,
    service_loop,
    staging_directory,
    validate_generated_video,
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
            # Existing external work always consumes the sole admission slot,
            # including an uncertain submission awaiting operator resolution.
            active = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.status.in_(GENERATION_ACTIVE))
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
            last_video_id = await session.scalar(
                select(SceneGeneration.video_id)
                .join(
                    GenerationAttempt,
                    GenerationAttempt.generation_id == SceneGeneration.id,
                )
                .order_by(GenerationAttempt.created_at.desc(), GenerationAttempt.id.desc())
                .limit(1)
            )
            pending = (
                select(SceneGeneration)
                .where(SceneGeneration.status == "CREATED")
                .order_by(SceneGeneration.created_at, SceneGeneration.id)
            )
            generation = None
            if last_video_id is not None:
                generation = await session.scalar(
                    pending.where(SceneGeneration.video_id != last_video_id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            if generation is None:
                generation = await session.scalar(
                    pending.with_for_update(skip_locked=True).limit(1)
                )
            if generation is None:
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
            generation = await session.get(SceneGeneration, generation_id)
            attempt = await session.scalar(
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation_id)
                .order_by(GenerationAttempt.attempt_no.desc())
                .limit(1)
            )
            return generation, attempt

    async def _update(self, generation_id: str, **values) -> bool:
        async with self.factory() as session, session.begin():
            generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
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
                and GENERATION_FLOW_RANK[desired_status]
                < GENERATION_FLOW_RANK[generation.status]
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
                select(GenerationAttempt)
                .where(GenerationAttempt.generation_id == generation_id)
                .order_by(GenerationAttempt.attempt_no.desc())
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
            return True

    async def _hold(self, generation_id: str, code: str, message: str) -> None:
        async with self.factory() as session, session.begin():
            generation = await session.get(SceneGeneration, generation_id, with_for_update=True)
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

    async def _finish(
        self, generation_id: str, status: str, code=None, message=None, asset_id=None
    ):
        async with self.factory() as session, session.begin():
            generation_ref = await session.get(SceneGeneration, generation_id)
            if generation_ref is None:
                return
            video = await session.get(Video, generation_ref.video_id, with_for_update=True)
            scene = await session.scalar(
                select(Scene).where(Scene.id == generation_ref.scene_id).with_for_update()
            )
            if video is None:
                return
            generation = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.id == generation_id)
                .with_for_update()
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
                output_asset = await session.get(Asset, asset_id, with_for_update=True)
                if output_asset is None or output_asset.status != "READY":
                    status = "FAILED"
                    code = "OUTPUT_ASSET_NOT_READY"
                    message = "Generated output was deleted or not committed"
                    asset_id = None
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
                ):
                    scene.selected_generation_id = generation.id
                    scene.revision += 1
                    video.revision += 1
            await refresh_video(session, generation.video_id)

    async def _retry_or_finish(self, generation_id: str, code: str, message: str) -> None:
        async with self.factory() as session, session.begin():
            generation_ref = await session.get(SceneGeneration, generation_id)
            if generation_ref is None:
                return
            video = await session.get(Video, generation_ref.video_id, with_for_update=True)
            generation = await session.scalar(
                select(SceneGeneration)
                .where(SceneGeneration.id == generation_id)
                .with_for_update()
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
                attempt.status = "FAILED"
                attempt.error_code = code
                attempt.error_message = message[:2000]
                attempt.finished_at = utcnow()
            if generation.status == "CANCEL_REQUESTED":
                generation.status = "CANCELLED"
                generation.phase = "CANCELLED"
                generation.finished_at = utcnow()
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

    async def _prepare_workflow(self, snapshot: dict) -> dict:
        workflow = copy.deepcopy(snapshot["workflow"])
        slots = snapshot.get("slots", {})
        counters: dict[str, int] = {}
        for asset in sorted(
            snapshot.get("assets", []), key=lambda item: item.get("order_index", 0)
        ):
            role = asset["role"]
            counters[role] = counters.get(role, 0) + 1
            name = f"{role}_{counters[role]}" if role.startswith("REFERENCE_") else role
            bindings = slots.get(name)
            if bindings is None:
                raise ValueError(f"WORKFLOW_ASSET_SLOT_MISSING: {name}")
            data = await self.store.get_bytes(asset["object_key"])
            check_checksum(data, asset.get("checksum"))
            uploaded = await self.adapter.upload(data, asset["filename"], asset["content_type"])
            if isinstance(uploaded, dict):
                uploaded = "/".join(filter(None, [uploaded.get("subfolder"), uploaded["name"]]))
            # One binding [node,input] or repeated bindings [[node,input], ...].
            if len(bindings) == 2 and isinstance(bindings[0], (str, int)):
                bindings = [bindings]
            for node, field in bindings:
                workflow[str(node)]["inputs"][field] = uploaded
        return workflow

    async def _submit_or_recover(self, generation, attempt) -> str | None:
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
                current = await session.get(SceneGeneration, generation.id, with_for_update=True)
                if current.claimed_by != self.owner or current.status != "DISPATCHING":
                    return None
                durable_attempt = await session.get(GenerationAttempt, attempt.id)
                durable_attempt.status = "DISPATCHING"
                current.phase = "SUBMITTING"
                current.lease_expires_at = lease_deadline(self.settings)
            try:
                prompt_id = await self.adapter.submit(workflow, attempt.client_id)
            except SubmissionUncertain as exc:
                await self._hold(generation.id, "SUBMISSION_UNCERTAIN", str(exc))
                return None
        async with self.factory() as session, session.begin():
            current = await session.get(SceneGeneration, generation.id, with_for_update=True)
            durable_attempt = await session.get(GenerationAttempt, attempt.id)
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
        return prompt_id

    async def _collect(self, generation, history: dict):
        if not await self._update(generation.id, status="COLLECTING", phase="COLLECTING"):
            return
        outputs = self.adapter.outputs(history, generation.input_snapshot.get("output_node"))
        if not outputs:
            await self._finish(
                generation.id, "FAILED", "COMFY_OUTPUT_MISSING", "Completed graph produced no video"
            )
            return
        output = outputs[0]
        data = await self.adapter.download(output)
        if len(data) > self.settings.max_upload_bytes:
            raise ValueError("OUTPUT_TOO_LARGE")
        async with staging_directory(self.settings, f"generation-{generation.id}-") as directory:
            path = directory / "output.mp4"
            await asyncio.to_thread(path.write_bytes, data)
            metadata = await self.ffmpeg.probe(path)
        try:
            metadata = await validate_generated_video(
                data,
                output["filename"],
                getattr(self.settings, "ffprobe_binary", "ffprobe"),
                comfy_kind=output.get("kind"),
                metadata=metadata,
            )
        except ValueError as exc:
            await self._finish(generation.id, "FAILED", "COMFY_OUTPUT_INVALID", str(exc))
            return
        async with self.factory() as session:
            video = await session.get(Video, generation.video_id)
        asset_id = await save_output(
            self.factory,
            self.store,
            owner_id=generation.id,
            role="GENERATED_VIDEO",
            project_id=video.project_id,
            created_by=generation.created_by,
            data=data,
            metadata=metadata,
        )
        await self._finish(generation.id, "COMPLETED", asset_id=asset_id)

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

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    adapter = ComfyAdapter(settings)
    try:
        await service_loop(
            Dispatcher(SessionFactory, adapter, AssetStore(settings), FFmpeg(settings), settings),
            settings.worker_poll_seconds,
        )
    finally:
        await adapter.close()


if __name__ == "__main__":
    asyncio.run(main())

