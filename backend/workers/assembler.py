"""Lease-based FFmpeg assembler using only immutable manifest data."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import or_, select

from apps.api.app.db.models import Asset, FinalVideo, Scene, SceneGeneration, Video, utcnow
from apps.api.app.services.assembly_service import manifest_hash
from apps.api.app.services.deletion_state import is_logically_deleted
from apps.api.app.services.generation_freshness import (
    is_assembly_source_current,
    lock_generation_dependencies,
    restore_video_after_assembly,
)
from workers.common import (
    check_checksum,
    lease_deadline,
    lock_scheduler,
    mark_unpublished_output_for_retention,
    require_staging_space,
    save_output_file,
    service_loop,
    staging_directory,
    worker_id,
)

logger = logging.getLogger(__name__)
FINAL_TERMINAL = frozenset({"READY", "FAILED", "CANCELLED"})


class Assembler:
    def __init__(self, factory, store, ffmpeg, settings):
        self.factory = factory
        self.store = store
        self.ffmpeg = ffmpeg
        self.settings = settings
        self.owner = worker_id("assembler")

    async def _lock_final_context(self, session, final_id: str):
        """Lock Video, dependencies, source rows, then FinalVideo for lifecycle checks."""
        reference = (
            await session.execute(
                select(FinalVideo.video_id, FinalVideo.manifest).where(
                    FinalVideo.id == final_id
                )
            )
        ).one_or_none()
        if reference is None:
            return None, None, True
        video_id, manifest = reference
        video = await session.scalar(
            select(Video)
            .where(Video.id == video_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if video is None:
            return None, None, True
        await lock_generation_dependencies(session, video)

        scenes_manifest = manifest.get("scenes") if isinstance(manifest, dict) else None
        valid_manifest = isinstance(scenes_manifest, list) and bool(scenes_manifest)
        scene_ids = []
        generation_ids = []
        if valid_manifest:
            for entry in scenes_manifest:
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("scene_id"), str)
                    or not isinstance(entry.get("generation_id"), str)
                ):
                    valid_manifest = False
                    break
                scene_ids.append(entry["scene_id"])
                generation_ids.append(entry["generation_id"])
        scene_ids = sorted(set(scene_ids))
        generation_ids = sorted(set(generation_ids))
        scenes = list(
            (
                await session.scalars(
                    select(Scene)
                    .where(Scene.id.in_(scene_ids), Scene.video_id == video.id)
                    .order_by(Scene.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        ) if scene_ids else []
        generations = list(
            (
                await session.scalars(
                    select(SceneGeneration)
                    .where(
                        SceneGeneration.id.in_(generation_ids),
                        SceneGeneration.video_id == video.id,
                    )
                    .order_by(SceneGeneration.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        ) if generation_ids else []
        scene_by_id = {scene.id: scene for scene in scenes}
        generation_by_id = {generation.id: generation for generation in generations}
        final = await session.scalar(
            select(FinalVideo)
            .where(FinalVideo.id == final_id, FinalVideo.video_id == video.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        hidden = (
            final is None
            or not valid_manifest
            or final.manifest != manifest
            or len(scene_by_id) != len(scene_ids)
            or len(generation_by_id) != len(generation_ids)
            or await is_logically_deleted(session, "VIDEO", video.id)
        )
        if final is not None and valid_manifest:
            for entry in scenes_manifest:
                generation = generation_by_id.get(entry["generation_id"])
                if (
                    entry["scene_id"] not in scene_by_id
                    or generation is None
                    or generation.scene_id != entry["scene_id"]
                ):
                    hidden = True
                    break
                if await is_logically_deleted(
                    session, "SCENE", entry["scene_id"], video.id
                ) or await is_logically_deleted(
                    session, "GENERATION", entry["generation_id"], video.id
                ):
                    hidden = True
                    break
        if final is not None and not hidden:
            hidden = await is_logically_deleted(
                session, "FINAL_VIDEO", final.id, video.id
            )
        return video, final, hidden

    async def _final_allows_output(self, final_id: str) -> bool:
        async with self.factory() as session, session.begin():
            _, final, hidden = await self._lock_final_context(session, final_id)
            return bool(
                not hidden
                and final is not None
                and final.claimed_by == self.owner
                and final.status not in FINAL_TERMINAL
                and final.status != "CANCEL_REQUESTED"
            )

    async def claim(self) -> str | None:
        """Claim a single queued final assembly.

        Concurrency invariant:
        Advisory lock 971032 serializes claim transactions to prevent race conditions.
        Concurrent workers may claim different queued finals for distinct videos in
        parallel, but must never claim the same final video. The partial unique index
        uq_final_videos_active_video enforces that each video has at most one active assembly.
        """
        async with self.factory() as session, session.begin():
            await lock_scheduler(session, 971032)
            now = utcnow()
            candidate = (
                await session.execute(
                    select(FinalVideo.id, FinalVideo.status, FinalVideo.lease_expires_at)
                    .where(
                        or_(
                            FinalVideo.status == "QUEUED",
                            (
                                FinalVideo.status.in_({"ASSEMBLING", "CANCEL_REQUESTED"})
                                & or_(
                                    FinalVideo.lease_expires_at.is_(None),
                                    FinalVideo.lease_expires_at <= now,
                                )
                            ),
                        )
                    )
                    .order_by(FinalVideo.created_at, FinalVideo.id)
                    .limit(1)
                )
            ).first()
            if candidate is None:
                return None
            video, final, hidden = await self._lock_final_context(session, candidate.id)
            if video is None or final is None or hidden:
                return None
            expiry = final.lease_expires_at
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=now.tzinfo)
            if final.status not in {"QUEUED", "ASSEMBLING", "CANCEL_REQUESTED"} or (
                final.status != "QUEUED" and expiry is not None and expiry > now
            ):
                return None
            final.claimed_by = self.owner
            final.lease_expires_at = lease_deadline(self.settings)
            final.attempt_count += 1
            final.revision += 1
            if final.status != "CANCEL_REQUESTED":
                final.status = "ASSEMBLING"
                final.phase = "PREPARING"
                final.started_at = final.started_at or now
                final.progress_current = 0
                final.progress_total = len(final.manifest.get("scenes", [])) + 3
                final.progress_updated_at = now
            return final.id

    async def _read(self, final_id: str) -> FinalVideo | None:
        async with self.factory() as session:
            return await session.get(FinalVideo, final_id)

    async def _phase(self, final_id: str, phase: str, current: int) -> bool:
        async with self.factory() as session, session.begin():
            _, final, hidden = await self._lock_final_context(session, final_id)
            if (
                final is None
                or hidden
                or final.claimed_by != self.owner
                or final.status in FINAL_TERMINAL
            ):
                return False
            if final.status == "CANCEL_REQUESTED":
                return False
            final.phase = phase
            final.progress_current = min(current, final.progress_total)
            final.progress_updated_at = utcnow()
            final.lease_expires_at = lease_deadline(self.settings)
            final.revision += 1
            return True

    async def _heartbeat(self, final_id: str) -> None:
        while True:
            await asyncio.sleep(max(1, self.settings.lease_seconds / 3))
            async with self.factory() as session, session.begin():
                _, final, hidden = await self._lock_final_context(session, final_id)
                if (
                    final is None
                    or hidden
                    or final.claimed_by != self.owner
                    or final.status in FINAL_TERMINAL
                ):
                    return
                final.lease_expires_at = lease_deadline(self.settings)

    async def _finish(
        self,
        final_id: str,
        status: str,
        *,
        asset_id: str | None = None,
        code: str | None = None,
        message: str | None = None,
    ) -> None:
        async with self.factory() as session, session.begin():
            video, final, hidden = await self._lock_final_context(session, final_id)
            if (
                final is None
                or video is None
                or final.claimed_by != self.owner
                or final.status in FINAL_TERMINAL
            ):
                if asset_id is not None:
                    await mark_unpublished_output_for_retention(session, asset_id)
                return
            video_deleted = await is_logically_deleted(session, "VIDEO", video.id)
            if hidden and status in {"READY", "CANCELLED"}:
                status = "CANCELLED"
                code = "RESOURCE_DELETED"
                message = "Assembly output was discarded because its source was deleted"
                # Keep the hidden output reference so cleanup can discover the
                # Asset persisted before this final tombstone check.
            if final.status == "CANCEL_REQUESTED" and not hidden:
                status = "CANCELLED"
                if asset_id is not None:
                    await mark_unpublished_output_for_retention(session, asset_id)
                    asset_id = None
            if status == "READY" and asset_id:
                output_asset = await session.get(Asset, asset_id, with_for_update=True)
                if (
                    output_asset is None
                    or output_asset.status != "READY"
                    or output_asset.project_id != video.project_id
                ):
                    status = "FAILED"
                    code = "OUTPUT_ASSET_NOT_READY"
                    message = "Assembled output was deleted or not committed"
                    asset_id = None
            final.status = status
            final.phase = status
            final.output_asset_id = asset_id
            final.error_code = code
            final.error_message = message[:2000] if message else None
            final.finished_at = utcnow()
            final.progress_updated_at = final.finished_at
            if status == "READY":
                final.progress_current = final.progress_total
            final.claimed_by = None
            final.lease_expires_at = None
            final.revision += 1

            if status == "READY":
                if await is_assembly_source_current(session, video, final.manifest):
                    video.current_final_video_id = final.id
                    video.status = "READY"
                else:
                    video.status = "DIRTY"
            elif not hidden and not video_deleted:
                await restore_video_after_assembly(
                    session,
                    video,
                    final.manifest,
                    "FAILED"
                    if status == "FAILED"
                    else "SCENES_READY"
                    if status == "CANCELLED"
                    else "DIRTY",
                )

    async def _retry_or_finish(self, final_id: str, code: str, message: str) -> None:
        async with self.factory() as session, session.begin():
            video, final, hidden = await self._lock_final_context(session, final_id)
            if (
                final is None
                or video is None
                or final.claimed_by != self.owner
                or final.status in FINAL_TERMINAL
            ):
                return
            now = utcnow()
            if hidden or final.status == "CANCEL_REQUESTED":
                final.status = "CANCELLED"
                final.phase = "CANCELLED"
                final.finished_at = now
            elif final.attempt_count < self.settings.max_assembly_attempts:
                final.status = "QUEUED"
                final.phase = "RETRY_PENDING"
            else:
                final.status = "FAILED"
                final.phase = "FAILED"
                final.finished_at = now
            final.error_code = code
            final.error_message = message[:2000]
            final.progress_updated_at = now
            final.claimed_by = None
            final.lease_expires_at = None
            final.revision += 1
            if hidden:
                final.error_code = "RESOURCE_DELETED"
                final.error_message = "Assembly was stopped because its source was deleted"
            elif final.status == "FAILED":
                await restore_video_after_assembly(session, video, final.manifest, "FAILED")
            elif final.status == "CANCELLED":
                await restore_video_after_assembly(session, video, final.manifest, "SCENES_READY")

    async def process(self, final_id: str) -> None:
        final = await self._read(final_id)
        if final is None or final.claimed_by != self.owner:
            return
        if final.status == "CANCEL_REQUESTED":
            await self._finish(final_id, "CANCELLED")
            return
        if manifest_hash(final.manifest) != final.manifest_hash:
            await self._finish(
                final_id,
                "FAILED",
                code="ASSEMBLY_MANIFEST_TAMPERED",
                message="Stored manifest hash does not match its content",
            )
            return
        scene_entries = final.manifest.get("scenes")
        if not isinstance(scene_entries, list) or not scene_entries:
            await self._finish(
                final_id,
                "FAILED",
                code="ASSEMBLY_MANIFEST_INVALID",
                message="Assembly manifest has no scene inputs",
            )
            return

        output_limit = getattr(self.settings, "max_generated_output_bytes", 500 * 1024**2)
        reserve_bytes = getattr(self.settings, "min_free_disk_bytes", 0)
        scene_size_bounds: list[int] = []
        for entry in scene_entries:
            limit = getattr(self.settings, "max_generated_output_bytes", 500 * 1024**2)
            expected_size = entry.get("asset_size_bytes")
            if expected_size is not None and (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size <= 0
                or expected_size > limit
            ):
                raise ValueError("ASSEMBLY_ASSET_SIZE_INVALID")
            scene_size_bounds.append(expected_size if expected_size is not None else limit)
        background = final.manifest.get("background_audio")
        background_limit = getattr(self.settings, "max_upload_bytes", 500 * 1024**2)
        background_size = background.get("asset_size_bytes") if background else None
        if background_size is not None and (
            isinstance(background_size, bool)
            or not isinstance(background_size, int)
            or background_size <= 0
            or background_size > background_limit
        ):
            raise ValueError("ASSEMBLY_AUDIO_SIZE_INVALID")
        background_size_bound = (
            (background_size if background_size is not None else background_limit)
            if background
            else 0
        )
        remaining_staging_bytes = sum(scene_size_bounds) + background_size_bound + output_limit

        async with staging_directory(self.settings, f"assembly-{final_id}-") as directory:
            inputs: list[Path] = []
            for index, entry in enumerate(scene_entries):
                size_bound = scene_size_bounds[index]
                expected_size = entry.get("asset_size_bytes")
                await require_staging_space(directory, remaining_staging_bytes, reserve_bytes)
                current = await self._read(final_id)
                if current is None or current.status == "CANCEL_REQUESTED":
                    await self._finish(final_id, "CANCELLED")
                    return
                path = directory / f"scene-{index:03d}.mp4"
                if hasattr(self.store, "download_to_path"):
                    actual = await self.store.download_to_path(
                        entry["asset_object_key"],
                        path,
                        size_bound,
                    )
                    if actual["checksum"] != entry["asset_checksum"]:
                        raise ValueError("ASSET_CHECKSUM_MISMATCH")
                    if expected_size is not None and actual["size"] != expected_size:
                        raise ValueError("ASSET_SIZE_MISMATCH")
                else:
                    data = await self.store.get_bytes(entry["asset_object_key"])
                    if len(data) > size_bound or (
                        expected_size is not None and len(data) != expected_size
                    ):
                        raise ValueError("ASSET_SIZE_MISMATCH")
                    check_checksum(data, entry["asset_checksum"])
                    await asyncio.to_thread(path.write_bytes, data)
                remaining_staging_bytes -= size_bound
                inputs.append(path)
                if not await self._phase(final_id, "MATERIALIZING", index + 1):
                    await self._finish(final_id, "CANCELLED")
                    return

            config = dict(final.manifest.get("assembly_config") or {})
            if background:
                background_path = directory / "background-audio"
                await require_staging_space(directory, remaining_staging_bytes, reserve_bytes)
                if hasattr(self.store, "download_to_path"):
                    actual = await self.store.download_to_path(
                        background["asset_object_key"],
                        background_path,
                        background_size_bound,
                    )
                    if actual["checksum"] != background["asset_checksum"]:
                        raise ValueError("ASSET_CHECKSUM_MISMATCH")
                    if background_size is not None and actual["size"] != background_size:
                        raise ValueError("ASSET_SIZE_MISMATCH")
                else:
                    data = await self.store.get_bytes(background["asset_object_key"])
                    if len(data) > background_size_bound or (
                        background_size is not None and len(data) != background_size
                    ):
                        raise ValueError("ASSET_SIZE_MISMATCH")
                    check_checksum(data, background["asset_checksum"])
                    await asyncio.to_thread(background_path.write_bytes, data)
                config["background_audio_path"] = str(background_path)
                remaining_staging_bytes -= background_size_bound
            if not await self._phase(final_id, "COMBINING", len(inputs) + 1):
                await self._finish(final_id, "CANCELLED")
                return
            if not await self._final_allows_output(final_id):
                await self._finish(final_id, "CANCELLED")
                return
            await require_staging_space(directory, output_limit, reserve_bytes)
            output = directory / "final.mp4"
            metadata = await self.ffmpeg.assemble(inputs, output, config)
            if not await self._phase(final_id, "VALIDATING", len(inputs) + 2):
                await self._finish(final_id, "CANCELLED")
                return
            if (await asyncio.to_thread(output.stat)).st_size > output_limit:
                raise ValueError("OUTPUT_TOO_LARGE")
            if not await self._final_allows_output(final_id):
                await self._finish(final_id, "CANCELLED")
                return
            async with self.factory() as session:
                video = await session.get(Video, final.video_id)
            asset_id = await save_output_file(
                self.factory,
                self.store,
                owner_id=final.id,
                role="FINAL_VIDEO",
                project_id=video.project_id,
                video_id=video.id,
                created_by=final.created_by,
                path=output,
                metadata=metadata,
                max_bytes=output_limit,
                claim_timeout_seconds=getattr(
                    self.settings, "asset_operation_claim_timeout_seconds", 900
                ),
            )
            await self._finish(final_id, "READY", asset_id=asset_id)

    async def run_once(self) -> bool:
        final_id = await self.claim()
        if final_id is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(final_id))
        try:
            await self.process(final_id)
        except asyncio.CancelledError:
            raise
        except (KeyError, ValueError) as exc:
            logger.exception("assembly_input_invalid", extra={"final_video_id": final_id})
            code = (
                "IMMUTABLE_OUTPUT_CONFLICT"
                if isinstance(exc, ValueError) and str(exc) == "IMMUTABLE_OUTPUT_CONFLICT"
                else "ASSEMBLY_INPUT_INVALID"
            )
            await self._finish(
                final_id,
                "FAILED",
                code=code,
                message=str(exc),
            )
        except Exception as exc:
            logger.exception("assembly_processing_failed", extra={"final_video_id": final_id})
            await self._retry_or_finish(final_id, "ASSEMBLY_FAILED", str(exc))
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return True


async def main() -> None:
    from apps.api.app.core.config import get_settings
    from apps.api.app.db.session import SessionFactory
    from apps.api.app.integrations.ffmpeg import FFmpeg
    from apps.api.app.integrations.minio import AssetStore

    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    worker = Assembler(SessionFactory, AssetStore(settings), FFmpeg(settings), settings)
    await service_loop(worker, settings.worker_poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
