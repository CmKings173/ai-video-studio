from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from apps.api.app.api.assets import delete_asset, upload_url
from apps.api.app.core.errors import AppError
from apps.api.app.db.models import (
    Asset,
    FinalVideo,
    FinalVideoScene,
    Project,
    Scene,
    SceneGeneration,
    User,
    Video,
    WorkflowRecord,
    utcnow,
)
from apps.api.app.integrations.minio import AssetStoreUnavailableError
from apps.api.app.schemas.api import AssetComplete, UploadRequest
from apps.api.app.services.asset_claims import (
    OUTPUT_WRITE_CLAIM,
    REPAIR_CLAIM,
    acquire_claim,
    clear_claim,
    owns_claim,
)
from apps.api.app.services.asset_retention import AssetRetentionService
from apps.api.app.services.asset_service import (
    asset_is_referenced,
    complete_asset,
    create_pending_asset,
    upload_staging_key,
)
from apps.api.app.services.idempotency import claim
from workers.common import save_output
from workers.reconciliation import AssetReconciler, ReconciliationReport


class FakeStore:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_count = 0
        self.deleted_calls: list[str] = []

    async def presign_upload(self, key: str, content_type: str) -> dict:
        return {"url": "https://upload.invalid", "fields": {"key": key, "type": content_type}}

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict:
        self.put_count += 1
        existing = self.objects.get(key)
        if existing is not None and existing != (data, content_type):
            raise RuntimeError("immutable conflict")
        self.objects[key] = (data, content_type)
        return {"key": key, "size": len(data), "checksum": hashlib.sha256(data).hexdigest()}

    put = put_bytes

    async def get_bytes(self, key: str, max_bytes: int | None = None) -> bytes:
        data = self.objects[key][0]
        if max_bytes is not None and len(data) > max_bytes:
            raise RuntimeError("too large")
        return data

    read = get_bytes

    async def head(self, key: str) -> dict:
        data, content_type = self.objects[key]
        return {
            "key": key,
            "size": len(data),
            "content_type": content_type,
            "checksum": hashlib.sha256(data).hexdigest(),
        }

    async def list_objects(self, prefix: str = "") -> list[dict]:
        return [
            {"key": key, "size": len(data), "last_modified": utcnow()}
            for key, (data, _content_type) in self.objects.items()
            if key.startswith(prefix)
        ]

    async def delete(self, key: str) -> None:
        self.deleted_calls.append(key)
        self.objects.pop(key, None)


class FlakyDeleteStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.fail_once = True

    async def delete(self, key: str) -> None:
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("MinIO request timed out")
        await super().delete(key)


class UploadUnavailableStore(FakeStore):
    async def head(self, key: str) -> dict:
        raise TimeoutError("MinIO request timed out")


class PromotionUnavailableStore(FakeStore):
    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict:
        if not key.startswith("staging/"):
            raise TimeoutError("MinIO request timed out")
        return await super().put_bytes(key, data, content_type)


class PendingUnavailableStore(FakeStore):
    async def get_bytes(self, key: str, max_bytes: int | None = None) -> bytes:
        raise TimeoutError("MinIO request timed out")


class TemporarilyUnavailableStore(FakeStore):
    def __init__(self, error: BaseException | None = None):
        super().__init__()
        self.error = error or TimeoutError("MinIO request timed out")

    async def head(self, key: str) -> dict:
        raise self.error


def png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (8, 6), "red").save(buffer, "PNG")
    return buffer.getvalue()


def settings(tmp_path):
    return SimpleNamespace(
        max_upload_bytes=1024 * 1024,
        max_media_seconds=60,
        ffprobe_binary="ffprobe",
        orphan_object_retention_hours=24,
        workspace_root=tmp_path,
        pending_asset_retention_hours=24,
        retention_failed_hours=24,
        deleted_asset_retention_hours=168,
        retention_deleting_retry_hours=1,
        retention_batch_size=100,
        asset_operation_claim_timeout_seconds=60,
    )


async def seed_owner(session_factory):
    async with session_factory() as session, session.begin():
        user = User(email="assets@example.test", name="Editor", password_hash="hash")
        session.add(user)
        await session.flush()
        project = Project(name="Campaign", description="", created_by=user.id)
        session.add(project)
        await session.flush()
        return user.id, project.id


@pytest.mark.asyncio
async def test_upload_intent_is_recoverable_and_complete_is_idempotent(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    data = png_bytes()
    request = UploadRequest(
        project_id=project_id,
        filename="product.png",
        content_type="image/png",
        size_bytes=len(data),
        role="PRODUCT_IMAGE",
    )
    async with session_factory() as session, session.begin():
        asset, _upload = await create_pending_asset(
            session, store, settings(tmp_path), request, user_id
        )
        asset_id, object_key = asset.id, asset.object_key
        assert asset.status == "PENDING_UPLOAD"

    store.objects[object_key] = (data, "image/png")
    checksum = hashlib.sha256(data).hexdigest()
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        completed = await complete_asset(
            session,
            store,
            settings(tmp_path),
            asset,
            AssetComplete(checksum_sha256=checksum),
        )
        assert completed.status == "READY"
        assert (completed.width, completed.height) == (8, 6)

    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        replay = await complete_asset(
            session,
            store,
            settings(tmp_path),
            asset,
            AssetComplete(checksum_sha256=checksum),
        )
        assert replay.id == asset_id
        assert replay.checksum == checksum


@pytest.mark.asyncio
async def test_complete_upload_keeps_validating_when_storage_is_unavailable(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = UploadUnavailableStore()
    request = UploadRequest(
        project_id=project_id,
        filename="product.png",
        content_type="image/png",
        size_bytes=10,
        role="PRODUCT_IMAGE",
    )
    async with session_factory() as session, session.begin():
        asset, _ = await create_pending_asset(session, store, settings(tmp_path), request, user_id)
        asset_id = asset.id
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        with pytest.raises(AppError) as error:
            await complete_asset(
                session, store, settings(tmp_path), asset, AssetComplete()
            )
        assert getattr(error.value, "status_code", None) == 503
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).status == "VALIDATING"


@pytest.mark.asyncio
async def test_complete_upload_promotion_unavailable_remains_retryable(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = PromotionUnavailableStore()
    data = png_bytes()
    request = UploadRequest(
        project_id=project_id,
        filename="product.png",
        content_type="image/png",
        size_bytes=len(data),
        role="PRODUCT_IMAGE",
    )
    async with session_factory() as session, session.begin():
        asset, _ = await create_pending_asset(session, store, settings(tmp_path), request, user_id)
        asset_id = asset.id
        staging = upload_staging_key(asset)
    store.objects[staging] = (data, "image/png")
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        with pytest.raises(AppError) as error:
            await complete_asset(session, store, settings(tmp_path), asset, AssetComplete())
        assert getattr(error.value, "status_code", None) == 503
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).status == "VALIDATING"


@pytest.mark.asyncio
async def test_complete_upload_rejects_active_operation_claim(session_factory, tmp_path):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="busy.png",
            content_type="image/png",
            object_key="assets/busy.png",
            status="PENDING_UPLOAD",
            size_bytes=10,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
        claim_id = await acquire_claim(
            session,
            asset_id,
            REPAIR_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
        )

    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        with pytest.raises(AppError) as error:
            await complete_asset(session, store, settings(tmp_path), asset, AssetComplete())

    assert error.value.code == "ASSET_OPERATION_BUSY"
    assert error.value.status_code == 409
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "PENDING_UPLOAD"
        assert owns_claim(row, claim_id, REPAIR_CLAIM)


@pytest.mark.asyncio
async def test_complete_upload_lost_claim_after_promotion_deletes_before_purge(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    data = png_bytes()
    request = UploadRequest(
        project_id=project_id,
        filename="race.png",
        content_type="image/png",
        size_bytes=len(data),
        role="PRODUCT_IMAGE",
    )
    store = FakeStore()
    async with session_factory() as session, session.begin():
        asset, _ = await create_pending_asset(session, store, settings(tmp_path), request, user_id)
        asset_id = asset.id
        staging_key = upload_staging_key(asset)
        canonical_key = asset.object_key
        assert asset.checksum is None
    store.objects[staging_key] = (data, "image/png")

    put_started = asyncio.Event()
    row_stolen = asyncio.Event()
    original_put = store.put_bytes

    async def coordinated_put(key, bytes_data, content_type):
        result = await original_put(key, bytes_data, content_type)
        if key == canonical_key:
            put_started.set()
            await row_stolen.wait()
        return result

    store.put_bytes = coordinated_put

    async def run_complete():
        async with session_factory() as session:
            asset = await session.get(Asset, asset_id)
            with pytest.raises(AppError) as error:
                await complete_asset(session, store, settings(tmp_path), asset, AssetComplete())
            return error.value

    async def steal_row():
        await put_started.wait()
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id, with_for_update=True)
            row.status = "DELETING"
            row.delete_claimed_at = utcnow()
            clear_claim(row)
        row_stolen.set()

    error, _ = await asyncio.gather(run_complete(), steal_row())

    assert error.code == "ASSET_OPERATION_BUSY"
    assert error.status_code == 409
    retention = AssetRetentionService(session_factory, store, settings(tmp_path))
    assert await retention.finalize(asset_id, now=utcnow())
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETED"
        assert row.purged_at is not None
        assert row.checksum is None
        assert row.operation_claim_id is None
    assert canonical_key not in store.objects


@pytest.mark.asyncio
async def test_reconciler_repairs_pending_upload_and_marks_missing_ready_asset_failed(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    data = png_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    async with session_factory() as session, session.begin():
        repairable = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="repair.png",
            content_type="image/png",
            object_key="uploads/repair.png",
            status="PENDING_UPLOAD",
            size_bytes=len(data),
            checksum=checksum,
            created_by=user_id,
        )
        missing = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="missing.png",
            content_type="image/png",
            object_key="uploads/missing.png",
            status="READY",
            size_bytes=len(data),
            checksum=checksum,
            created_by=user_id,
        )
        session.add_all([repairable, missing])
        await session.flush()
        repairable_id, missing_id = repairable.id, missing.id
    store.objects["uploads/repair.png"] = (data, "image/png")

    report = await AssetReconciler(session_factory, store, settings(tmp_path)).run()

    assert report.repaired_assets == [repairable_id]
    assert report.missing_objects == ["uploads/missing.png"]
    async with session_factory() as session:
        assert (await session.get(Asset, repairable_id)).status == "READY"
        assert (await session.get(Asset, missing_id)).status == "FAILED"


@pytest.mark.asyncio
async def test_reconciler_marks_corrupt_ready_object_failed(session_factory, tmp_path):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    expected = png_bytes()
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="corrupt.png",
            content_type="image/png",
            object_key="assets/corrupt.png",
            status="READY",
            size_bytes=len(expected),
            checksum=hashlib.sha256(expected).hexdigest(),
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
    store.objects["assets/corrupt.png"] = (b"corrupt", "image/png")
    report = await AssetReconciler(session_factory, store, settings(tmp_path)).run()
    assert report.corrupt_objects == ["assets/corrupt.png"]
    async with session_factory() as session:
        assert (await session.get(Asset, asset.id)).status == "FAILED"


@pytest.mark.parametrize(
    "storage_error",
    [
        TimeoutError("MinIO request timed out"),
        ConnectionError("connection reset by peer"),
        ClientError(
            {
                "Error": {"Code": "InternalError"},
                "ResponseMetadata": {"HTTPStatusCode": 503},
            },
            "HeadObject",
        ),
    ],
    ids=["timeout", "connection", "server-error"],
)
@pytest.mark.asyncio
async def test_reconciler_does_not_fail_ready_asset_when_storage_is_temporarily_unavailable(
    session_factory, tmp_path, storage_error
):
    user_id, project_id = await seed_owner(session_factory)
    store = TemporarilyUnavailableStore(storage_error)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="available-after-retry.png",
            content_type="image/png",
            object_key="assets/temporarily-unavailable.png",
            status="READY",
            size_bytes=10,
            checksum="a" * 64,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()

    report = await AssetReconciler(session_factory, store, settings(tmp_path)).run(
        inspect_orphans=False
    )

    assert report.unavailable_objects == [asset.object_key]
    async with session_factory() as session:
        assert (await session.get(Asset, asset.id)).status == "READY"


@pytest.mark.asyncio
async def test_browser_upload_promotes_to_immutable_key_and_ready_replay_cannot_overwrite(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    first = png_bytes()
    request = UploadRequest(
        project_id=project_id,
        filename="product.png",
        content_type="image/png",
        size_bytes=len(first),
        role="PRODUCT_IMAGE",
    )
    async with session_factory() as session, session.begin():
        asset, _ = await create_pending_asset(session, store, settings(tmp_path), request, user_id)
        asset_id = asset.id
        canonical = asset.object_key
        staging = upload_staging_key(asset)
    store.objects[staging] = (first, "image/png")
    checksum = hashlib.sha256(first).hexdigest()
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        await complete_asset(
            session, store, settings(tmp_path), asset, AssetComplete(checksum_sha256=checksum)
        )
    assert canonical in store.objects
    assert store.objects[canonical][0] == first
    store.objects[staging] = (png_bytes(), "image/png")
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        replay = await complete_asset(session, store, settings(tmp_path), asset, AssetComplete())
        assert replay.status == "READY"
    assert store.objects[canonical][0] == first


@pytest.mark.asyncio
async def test_background_audio_is_a_protected_final_reference(session_factory, tmp_path):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        video = Video(
            project_id=project_id,
            title="Audio reference",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            brief="brief",
            created_by=user_id,
        )
        session.add(video)
        await session.flush()
        asset = Asset(
            project_id=project_id,
            kind="AUDIO",
            role="BACKGROUND_AUDIO",
            filename="music.wav",
            content_type="audio/wav",
            object_key="assets/music.wav",
            status="READY",
            size_bytes=10,
            checksum="a" * 64,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        workflow = WorkflowRecord(
            code="RETENTION_REF",
            mode="t2v",
            version="1",
            workflow={"1": {"class_type": "Text", "inputs": {"text": "prompt"}}},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="c" * 64,
            slot_map_hash="d" * 64,
            enabled=True,
            created_by=user_id,
        )
        session.add(workflow)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="prompt", duration_seconds=5)
        session.add(scene)
        await session.flush()
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode="t2v",
            workflow_id=workflow.id,
            generation_no=1,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={"workflow": workflow.workflow, "slots": {}, "assets": []},
            created_by=user_id,
        )
        session.add(generation)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="QUEUED",
            manifest={"background_audio": {"asset_id": asset.id}},
            manifest_hash="b" * 64,
            assembly_config={},
            background_audio_asset_id=asset.id,
            created_by=user_id,
        )
        session.add(final)
        await session.flush()
        # background_audio_asset_id alone protects asset (no FinalVideoScene referencing asset)
        assert await asset_is_referenced(session, asset.id)


@pytest.mark.asyncio
async def test_final_video_scene_is_a_protected_final_reference(session_factory, tmp_path):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        video = Video(
            project_id=project_id,
            title="Scene reference",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            brief="brief",
            created_by=user_id,
        )
        session.add(video)
        await session.flush()
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="scene.mp4",
            content_type="video/mp4",
            object_key="assets/scene.mp4",
            status="READY",
            size_bytes=10,
            checksum="b" * 64,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        workflow = WorkflowRecord(
            code="RETENTION_SCENE_REF",
            mode="t2v",
            version="1",
            workflow={"1": {"class_type": "Text", "inputs": {"text": "prompt"}}},
            slots={},
            required_slots=[],
            profile={},
            workflow_hash="d" * 64,
            slot_map_hash="e" * 64,
            enabled=True,
            created_by=user_id,
        )
        session.add(workflow)
        await session.flush()
        scene = Scene(video_id=video.id, scene_order=0, prompt="prompt", duration_seconds=5)
        session.add(scene)
        await session.flush()
        generation = SceneGeneration(
            video_id=video.id,
            scene_id=scene.id,
            mode="t2v",
            workflow_id=workflow.id,
            generation_no=1,
            operation="ORIGINAL",
            status="COMPLETED",
            phase="COMPLETED",
            input_snapshot={"workflow": workflow.workflow, "slots": {}, "assets": []},
            created_by=user_id,
        )
        session.add(generation)
        await session.flush()
        final = FinalVideo(
            video_id=video.id,
            version_no=1,
            status="QUEUED",
            manifest={},
            manifest_hash="c" * 64,
            assembly_config={},
            background_audio_asset_id=None,
            created_by=user_id,
        )
        session.add(final)
        await session.flush()
        session.add(
            FinalVideoScene(
                final_video_id=final.id,
                scene_id=scene.id,
                scene_order=0,
                generation_id=generation.id,
                asset_id=asset.id,
                asset_checksum=asset.checksum,
            )
        )
        await session.flush()
        # FinalVideoScene.asset_id alone protects asset (background_audio_asset_id is None)
        assert await asset_is_referenced(session, asset.id)


@pytest.mark.asyncio
async def test_retention_does_not_delete_asset_that_becomes_ready_before_claim(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="ready.png",
            content_type="image/png",
            object_key="assets/ready.png",
            status="PENDING",
            size_bytes=10,
            created_by=user_id,
            created_at=now - timedelta(days=3),
        )
        session.add(asset)
        await session.flush()
        asset.status = "READY"

    service = AssetRetentionService(session_factory, store, settings(tmp_path))
    assert await service.claim_batch(now=now) == []
    assert "assets/ready.png" not in store.objects


@pytest.mark.asyncio
async def test_retention_delete_failure_leaves_deleting_for_stale_retry(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FlakyDeleteStore()
    now = utcnow()
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="retry.png",
            content_type="image/png",
            object_key="assets/retry.png",
            status="PENDING",
            size_bytes=10,
            created_by=user_id,
            created_at=now - timedelta(days=3),
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
    store.objects["assets/retry.png"] = (b"retry", "image/png")
    service = AssetRetentionService(session_factory, store, settings(tmp_path))
    first = await service.cleanup(now=now)
    assert first.deleted_objects == 0
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        assert asset.status == "DELETING"
        asset.delete_claimed_at = now - timedelta(hours=2)
        asset.updated_at = now - timedelta(hours=2)
    second = await service.cleanup(now=now)
    assert second.deleted_objects == 1
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).status == "DELETED"


@pytest.mark.asyncio
async def test_retention_claims_before_object_delete_and_finalizes_only_claimed_asset(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="old.png",
            content_type="image/png",
            object_key="assets/old.png",
            status="PENDING",
            size_bytes=10,
            created_by=user_id,
            created_at=now - timedelta(days=3),
            updated_at=now - timedelta(days=3),
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
    store.objects["assets/old.png"] = (b"old", "image/png")

    service = AssetRetentionService(session_factory, store, settings(tmp_path))
    claimed = await service.claim_batch(now=now)
    assert [item.asset_id for item in claimed] == [asset_id]
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).status == "DELETING"

    await service.finalize(asset_id, now=now)
    async with session_factory() as session:
        finalized = await session.get(Asset, asset_id)
        assert finalized.status == "DELETED"
        assert finalized.deleted_at is not None
        assert finalized.purged_at is not None
    assert "assets/old.png" not in store.objects


@pytest.mark.asyncio
async def test_retention_skips_referenced_assets_and_respects_batch_limit(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        video = Video(
            project_id=project_id,
            title="Retention references",
            kind="LONG_VIDEO",
            target_duration=30,
            aspect_ratio="16:9",
            brief="brief",
            created_by=user_id,
        )
        session.add(video)
        await session.flush()
        assets = [
            Asset(
                project_id=project_id,
                kind="AUDIO",
                role="BACKGROUND_AUDIO",
                filename=f"old-{index}.wav",
                content_type="audio/wav",
                object_key=f"assets/old-{index}.wav",
                status="PENDING",
                size_bytes=10,
                created_by=user_id,
                created_at=utcnow() - timedelta(days=3),
                updated_at=utcnow() - timedelta(days=3),
            )
            for index in range(3)
        ]
        session.add_all(assets)
        await session.flush()
        session.add(
            FinalVideo(
                video_id=video.id,
                version_no=1,
                status="QUEUED",
                manifest={"schema_version": 1},
                manifest_hash="b" * 64,
                assembly_config={},
                background_audio_asset_id=assets[2].id,
                created_by=user_id,
            )
        )

    retention_settings = settings(tmp_path)
    retention_settings.retention_batch_size = 2
    service = AssetRetentionService(session_factory, FakeStore(), retention_settings)
    claimed = await service.claim_batch(now=utcnow())
    assert len(claimed) == 2
    assert all(item.asset_id != assets[2].id for item in claimed)


@pytest.mark.asyncio
async def test_save_output_repairs_ready_database_row_when_object_is_missing(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    data = b"generated-video-bytes"
    metadata = {
        "kind": "VIDEO",
        "has_video": True,
        "width": 480,
        "height": 864,
        "duration_seconds": 5.0,
    }

    asset_id = await save_output(
        session_factory,
        store,
        owner_id="generation-1",
        role="GENERATED_VIDEO",
        project_id=project_id,
        created_by=user_id,
        data=data,
        metadata=metadata,
    )
    async with session_factory() as session, session.begin():
        asset = await session.get(Asset, asset_id, with_for_update=True)
        asset.updated_at = utcnow() - timedelta(minutes=1)
    store.objects.clear()

    replay_id = await save_output(
        session_factory,
        store,
        owner_id="generation-1",
        role="GENERATED_VIDEO",
        project_id=project_id,
        created_by=user_id,
        data=data,
        metadata=metadata,
    )

    assert replay_id == asset_id
    assert store.put_count == 2
    async with session_factory() as session:
        repaired = await session.get(Asset, asset_id)
        assert repaired.status == "READY"
        assert repaired.checksum == hashlib.sha256(data).hexdigest()

@pytest.mark.asyncio
async def test_save_output_stale_writer_compensates_during_deleting_before_purge(
    session_factory, tmp_path
):
    from uuid import NAMESPACE_URL, uuid5

    user_id, project_id = await seed_owner(session_factory)
    data = b"retention-race-output"
    checksum = hashlib.sha256(data).hexdigest()
    role = "GENERATED_VIDEO"
    owner_id = "generation-retention-race"
    asset_id = str(uuid5(NAMESPACE_URL, f"ai-video-studio:{role}:{owner_id}"))
    canonical_key = f"outputs/{role.lower()}/{owner_id}/{checksum}.mp4"

    put_started = asyncio.Event()
    allow_put_complete = asyncio.Event()

    class CoordinatedStore(FakeStore):
        async def put_bytes(self, key, bytes_data, content_type):
            if key == canonical_key:
                put_started.set()
                await allow_put_complete.wait()
            return await super().put_bytes(key, bytes_data, content_type)

    store = CoordinatedStore()
    app_settings = settings(tmp_path)
    retention = AssetRetentionService(session_factory, store, app_settings)

    async def run_writer():
        with pytest.raises(ValueError) as error:
            await save_output(
                session_factory,
                store,
                owner_id=owner_id,
                role=role,
                project_id=project_id,
                created_by=user_id,
                data=data,
                metadata={"kind": "VIDEO", "has_video": True},
                claim_timeout_seconds=60,
            )
        return str(error.value)

    writer = asyncio.create_task(run_writer())
    await asyncio.wait_for(put_started.wait(), timeout=1)

    now = utcnow()
    async with session_factory() as session, session.begin():
        row = await session.get(Asset, asset_id, with_for_update=True)
        assert row.status == "PENDING_UPLOAD"
        assert row.operation_claim_id is not None
        row.created_at = now - timedelta(hours=25)
        row.updated_at = now - timedelta(hours=25)
        row.operation_claimed_at = now - timedelta(seconds=61)

    claimed = await retention.claim_batch(now=now)
    assert [item.asset_id for item in claimed] == [asset_id]

    await store.delete(canonical_key)
    assert canonical_key not in store.objects

    allow_put_complete.set()
    assert await writer == "ASSET_OPERATION_BUSY"

    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETING"
        assert row.operation_claim_id is None
    assert canonical_key not in store.objects

    assert await retention.finalize(asset_id, now=now + timedelta(seconds=1))
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETED"
        assert row.purged_at is not None
    assert canonical_key not in store.objects


@pytest.mark.asyncio
async def test_reconciler_reports_pending_storage_unavailable_without_marking_failed(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="pending.mp4",
            content_type="video/mp4",
            object_key="assets/pending.mp4",
            status="PENDING_UPLOAD",
            size_bytes=10,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
    report = await AssetReconciler(
        session_factory, PendingUnavailableStore(), settings(tmp_path)
    ).run(inspect_orphans=False)
    assert report.unavailable_objects == ["assets/pending.mp4"]
    assert report.corrupt_objects == []
    async with session_factory() as session:
        assert (await session.get(Asset, asset.id)).status == "PENDING_UPLOAD"


@pytest.mark.asyncio
async def test_reconciler_skips_missing_pending_asset_with_active_claim(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="claimed.mp4",
            content_type="video/mp4",
            object_key="assets/claimed.mp4",
            status="PENDING_UPLOAD",
            size_bytes=10,
            created_by=user_id,
            created_at=utcnow() - timedelta(hours=2),
        )
        session.add(asset)
        await session.flush()
        claim_id = await acquire_claim(
            session,
            asset.id,
            OUTPUT_WRITE_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
        )
        asset_id = asset.id

    report = await AssetReconciler(
        session_factory, FakeStore(), settings(tmp_path)
    ).run(inspect_orphans=False)

    assert report.missing_objects == []
    assert report.skipped_claimed_assets == [asset_id]
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "PENDING_UPLOAD"
        assert owns_claim(row, claim_id, OUTPUT_WRITE_CLAIM)


@pytest.mark.asyncio
async def test_retention_physical_purge_terminal_semantics_and_idempotent_retry(
    session_factory, tmp_path
):
    """Mandatory test: purge succeeds -> purged_at set;
    second cleanup does not claim or re-delete."""
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()
    cutoff_deleted = now - timedelta(hours=200)

    # Seed asset 1: soft-deleted 200 hours ago (> 168h cutoff)
    async with session_factory() as session, session.begin():
        asset1 = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="purge1.mp4",
            content_type="video/mp4",
            object_key="outputs/purge1.mp4",
            status="DELETED",
            deleted_at=cutoff_deleted,
            purged_at=None,
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset1)
        await session.flush()
        asset1_id = asset1.id
        store.objects[asset1.object_key] = (b"video-bytes", "video/mp4")

    service = AssetRetentionService(session_factory, store, settings(tmp_path))

    # Test 1: cleanup() -> object deleted, status DELETED, purged_at set
    result1 = await service.cleanup(now=now)
    assert result1.deleted_objects == 1
    assert asset1.object_key not in store.objects
    assert asset1.object_key in store.deleted_calls

    async with session_factory() as session:
        refreshed = await session.get(Asset, asset1_id)
        assert refreshed.status == "DELETED"
        assert refreshed.purged_at is not None
        assert refreshed.deleted_at == cutoff_deleted

    # Test 2 - critical regression: second cleanup() -> same asset NOT claimed,
    # store.delete NOT called again
    initial_deleted_calls = len(store.deleted_calls)
    result2 = await service.cleanup(now=now)
    assert result2.deleted_objects == 0
    assert len(result2.candidates) == 0
    assert len(store.deleted_calls) == initial_deleted_calls

    # Test 3: physical delete fails -> status remains DELETING, purged_at remains NULL
    async with session_factory() as session, session.begin():
        asset2 = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="purge2.mp4",
            content_type="video/mp4",
            object_key="outputs/purge2.mp4",
            status="DELETED",
            deleted_at=cutoff_deleted,
            purged_at=None,
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset2)
        await session.flush()
        asset2_id = asset2.id
        store.objects[asset2.object_key] = (b"video-bytes-2", "video/mp4")

    flaky_store = FlakyDeleteStore()
    flaky_store.objects[asset2.object_key] = (b"video-bytes-2", "video/mp4")
    flaky_service = AssetRetentionService(session_factory, flaky_store, settings(tmp_path))

    # Cleanup fails object delete
    failed_result = await flaky_service.cleanup(now=now)
    assert failed_result.deleted_objects == 0
    assert failed_result.retained_records == 1

    async with session_factory() as session:
        deleting_row = await session.get(Asset, asset2_id)
        assert deleting_row.status == "DELETING"
        assert deleting_row.purged_at is None
        assert deleting_row.delete_claimed_at == now

    # Immediate second cleanup must NOT claim it because delete_claimed_at is recent
    immediate_retry = await flaky_service.cleanup(now=now + timedelta(minutes=5))
    assert len(immediate_retry.candidates) == 0

    # Retry later (after 1 hour retry interval) succeeds
    retry_later = await flaky_service.cleanup(now=now + timedelta(hours=2))
    assert retry_later.deleted_objects == 1

    async with session_factory() as session:
        final_row = await session.get(Asset, asset2_id)
        assert final_row.status == "DELETED"
        assert final_row.purged_at is not None
    assert asset2.object_key not in flaky_store.objects


@pytest.mark.asyncio
async def test_compensation_delete_failure_is_durably_retried_by_retention(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    data = b"orphan-after-terminal-purge"
    checksum = hashlib.sha256(data).hexdigest()
    store = FlakyDeleteStore()
    object_key = "outputs/retry-compensation.mp4"
    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="GENERATED_VIDEO",
            filename="retry-compensation.mp4",
            content_type="video/mp4",
            object_key=object_key,
            status="DELETED",
            deleted_at=utcnow() - timedelta(hours=200),
            purged_at=utcnow(),
            checksum=checksum,
            size_bytes=len(data),
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
    store.objects[object_key] = (data, "video/mp4")

    report = ReconciliationReport()
    reconciler = AssetReconciler(session_factory, store, settings(tmp_path))
    await reconciler._compensate_promoted(
        asset_id,
        "stale-repair-claim",
        object_key,
        checksum,
        len(data),
        report,
    )

    assert report.compensation_failures == [object_key]
    assert object_key in store.objects
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETING"
        assert row.purged_at is None
        assert row.delete_claimed_at is not None

    retry_settings = settings(tmp_path)
    retry_settings.retention_deleting_retry_hours = 0
    result = await AssetRetentionService(
        session_factory, store, retry_settings
    ).cleanup(now=utcnow() + timedelta(seconds=1))

    assert result.deleted_objects == 1
    async with session_factory() as session:
        final_row = await session.get(Asset, asset_id)
        assert final_row.status == "DELETED"
        assert final_row.purged_at is not None
    assert object_key not in store.objects


@pytest.mark.asyncio
async def test_retention_cleanup_returns_has_more_true_when_more_eligible_assets_remain(
    session_factory, tmp_path
):
    """Mandatory test: batch_size = 2 with 3+ eligible assets returns
    has_more = True, then False."""
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()
    cutoff = now - timedelta(hours=200)

    async with session_factory() as session, session.begin():
        for i in range(3):
            asset = Asset(
                project_id=project_id,
                kind="VIDEO",
                role="GENERATED_VIDEO",
                filename=f"batch_{i}.mp4",
                content_type="video/mp4",
                object_key=f"outputs/batch_{i}.mp4",
                status="DELETED",
                deleted_at=cutoff,
                purged_at=None,
                size_bytes=100,
                created_by=user_id,
            )
            session.add(asset)
            store.objects[asset.object_key] = (b"bytes", "video/mp4")

    test_settings = settings(tmp_path)
    test_settings.retention_batch_size = 2
    service = AssetRetentionService(session_factory, store, test_settings)

    # First cleanup processes 2 items, has_more must be True
    res1 = await service.cleanup(now=now)
    assert len(res1.candidates) == 2
    assert res1.deleted_objects == 2
    assert res1.has_more is True

    # Second cleanup processes the 3rd item, has_more must be False
    res2 = await service.cleanup(now=now)
    assert len(res2.candidates) == 1
    assert res2.deleted_objects == 1
    assert res2.has_more is False


@pytest.mark.asyncio
async def test_reconciliation_cleanup_staging_deletes_expired_unreferenced_staging(
    session_factory, tmp_path
):
    """Mandatory test: stale failed/pending upload does not leave staging bytes forever."""
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()

    # Asset 1: FAILED (terminal)
    # Asset 2: PENDING_UPLOAD (active in-flight)
    async with session_factory() as session, session.begin():
        asset1 = Asset(
            id="term-failed-asset",
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="term.mp4",
            content_type="video/mp4",
            object_key="assets/term.mp4",
            status="FAILED",
            failed_at=now - timedelta(hours=48),
            size_bytes=100,
            created_by=user_id,
        )
        asset2 = Asset(
            id="active-upload-asset",
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="active.mp4",
            content_type="video/mp4",
            object_key="assets/active.mp4",
            status="PENDING_UPLOAD",
            size_bytes=100,
            created_by=user_id,
        )
        session.add_all([asset1, asset2])

    staging1 = "staging/assets/term-failed-asset/upload"
    staging2 = "staging/assets/active-upload-asset/upload"
    store.objects[staging1] = (b"staging1-bytes", "video/mp4")
    store.objects[staging2] = (b"staging2-bytes", "video/mp4")

    reconciler = AssetReconciler(session_factory, store, settings(tmp_path))

    # Override list_objects to simulate older timestamps (> 24h orphan retention cutoff)
    async def list_old_staging(prefix=""):
        return [
            {"key": staging1, "size": 100, "last_modified": now - timedelta(hours=48)},
            {"key": staging2, "size": 100, "last_modified": now - timedelta(hours=48)},
        ]
    store.list_objects = list_old_staging

    deleted = await reconciler.cleanup_staging()
    assert staging1 in deleted
    assert staging1 not in store.objects
    # Active upload staging is preserved despite age
    assert staging2 not in deleted
    assert staging2 in store.objects


@pytest.mark.asyncio
async def test_delete_asset_endpoint_is_idempotent_on_deleting_asset(session_factory):
    """Mandatory test: simulate asset = DELETING, DELETE /asset ->
    expected: status still DELETING."""
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="deleting.mp4",
            content_type="video/mp4",
            object_key="assets/deleting.mp4",
            status="DELETING",
            delete_claimed_at=utcnow(),
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    # Call direct delete_asset
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        await delete_asset(asset_id, user=user, session=session)

    # In DB, status must STILL be DELETING (not overwritten to DELETED)
    async with session_factory() as session:
        refreshed = await session.get(Asset, asset_id)
        assert refreshed.status == "DELETING"


@pytest.mark.asyncio
async def test_delete_asset_endpoint_rejects_active_operation_claim(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="busy-delete.mp4",
            content_type="video/mp4",
            object_key="assets/busy-delete.mp4",
            status="PENDING_UPLOAD",
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id
        claim_id = await acquire_claim(
            session,
            asset_id,
            REPAIR_CLAIM,
            timeout_seconds=60,
            allowed_statuses={"PENDING_UPLOAD"},
        )

    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        with pytest.raises(AppError) as exc_info:
            await delete_asset(
                asset_id,
                user=user,
                session=session,
                settings=settings(tmp_path),
            )
    assert exc_info.value.code == "ASSET_OPERATION_BUSY"
    assert exc_info.value.status_code == 409

    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "PENDING_UPLOAD"
        assert owns_claim(row, claim_id, REPAIR_CLAIM)


@pytest.mark.asyncio
async def test_upload_replay_rejects_deleting_asset(session_factory, tmp_path):
    """Mandatory test: idempotency replay with asset status DELETING
    returns 409 ASSET_STATE_CONFLICT."""
    from apps.api.app.services.idempotency import complete as complete_idempotency
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    app_settings = settings(tmp_path)
    app_settings.idempotency_hours = 24

    req_payload = UploadRequest(
        role="PRODUCT_IMAGE",
        filename="replay.png",
        content_type="image/png",
        size_bytes=100,
        project_id=project_id,
    )

    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="replay.png",
            content_type="image/png",
            object_key="assets/replay.png",
            status="DELETING",
            delete_claimed_at=utcnow(),
            size_bytes=100,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

        # Seed idempotency record completed pointing to this DELETING asset
        record, _ = await claim(
            session,
            user_id=user_id,
            operation="asset-upload-url",
            key="replay-key-123",
            payload=req_payload.model_dump(mode="json"),
            hours=24,
        )
        complete_idempotency(record, {"asset_id": asset_id}, 201)

    # Replay upload_url call
    async with session_factory() as session, session.begin():
        user = await session.get(User, user_id)
        with pytest.raises(AppError) as exc_info:
            await upload_url(
                payload=req_payload,
                user=user,
                session=session,
                key="replay-key-123",
                settings=app_settings,
                asset_store=store,
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.code == "ASSET_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_save_output_guards_against_concurrent_deleting_and_storage_unavailable(
    session_factory, tmp_path
):
    """Mandatory tests: save_output rejects DELETING transition,
    and storage unavailable does not downgrade READY."""
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    data = b"video-content-data"
    metadata = {
        "kind": "VIDEO",
        "has_video": True,
        "width": 640,
        "height": 360,
        "duration_seconds": 3.0,
    }

    # 1. State conflict race: asset becomes DELETING before final commit
    # We subclass store to simulate concurrent state change during put_bytes
    class MutatingStore(FakeStore):
        async def put_bytes(self, key, data, content_type):
            # Simulate retention claiming asset concurrently
            async with session_factory() as session, session.begin():
                row = await session.get(Asset, asset_id, with_for_update=True)
                row.status = "DELETING"
            return await super().put_bytes(key, data, content_type)

    import uuid
    from uuid import NAMESPACE_URL, uuid5
    role = "GENERATED_VIDEO"
    owner_id = f"gen-{uuid.uuid4().hex}"
    asset_id = str(uuid5(NAMESPACE_URL, f"ai-video-studio:{role}:{owner_id}"))

    mutating_store = MutatingStore()
    with pytest.raises(ValueError, match="ASSET_STATE_CONFLICT"):
        await save_output(
            session_factory,
            mutating_store,
            owner_id=owner_id,
            role=role,
            project_id=project_id,
            created_by=user_id,
            data=data,
            metadata=metadata,
        )

    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETING"  # Must NOT be READY

    assert await AssetRetentionService(
        session_factory, mutating_store, settings(tmp_path)
    ).finalize(asset_id, now=utcnow())
    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETED"
        assert row.purged_at is not None
    assert row.object_key not in mutating_store.objects

    # 2. READY asset + transient storage unavailable does NOT downgrade to PENDING_UPLOAD
    class UnavailableStore(FakeStore):
        async def get_bytes(self, key, max_bytes=None):
            raise AssetStoreUnavailableError("503 Service Unavailable")

    # Create a READY asset
    ready_owner = f"gen-ready-{uuid.uuid4().hex}"
    ready_id = await save_output(
        session_factory,
        store,
        owner_id=ready_owner,
        role=role,
        project_id=project_id,
        created_by=user_id,
        data=data,
        metadata=metadata,
    )

    async with session_factory() as session:
        ready_row = await session.get(Asset, ready_id)
        assert ready_row.status == "READY"

    # Save output replay with storage temporarily unavailable
    unavail_store = UnavailableStore()
    with pytest.raises(AssetStoreUnavailableError):
        await save_output(
            session_factory,
            unavail_store,
            owner_id=ready_owner,
            role=role,
            project_id=project_id,
            created_by=user_id,
            data=data,
            metadata=metadata,
        )

    # In DB, status MUST remain READY (not downgraded to PENDING_UPLOAD)
    async with session_factory() as session:
        refreshed_ready = await session.get(Asset, ready_id)
        assert refreshed_ready.status == "READY"


@pytest.mark.asyncio
async def test_reconciler_fresh_upload_grace_and_stale_upload_policy(session_factory, tmp_path):
    """Mandatory test: fresh PENDING_UPLOAD stays pending;
    stale PENDING_UPLOAD transitions to FAILED."""
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()

    async with session_factory() as session, session.begin():
        # Fresh asset (created 2 minutes ago < 15 min grace)
        fresh_asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="fresh.mp4",
            content_type="video/mp4",
            object_key="assets/fresh.mp4",
            status="PENDING_UPLOAD",
            created_at=now - timedelta(minutes=2),
            size_bytes=100,
            created_by=user_id,
        )
        # Stale asset (created 30 minutes ago > 15 min grace)
        stale_asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="REFERENCE_VIDEO",
            filename="stale.mp4",
            content_type="video/mp4",
            object_key="assets/stale.mp4",
            status="PENDING_UPLOAD",
            created_at=now - timedelta(minutes=30),
            size_bytes=100,
            created_by=user_id,
        )
        session.add_all([fresh_asset, stale_asset])
        await session.flush()
        fresh_id, stale_id = fresh_asset.id, stale_asset.id

    reconciler = AssetReconciler(session_factory, store, settings(tmp_path))
    report = await reconciler.run(inspect_orphans=False)

    # Fresh asset is NOT reported missing or corrupt
    assert "assets/fresh.mp4" not in report.missing_objects
    assert "assets/fresh.mp4" not in report.corrupt_objects

    # Stale asset IS reported missing
    assert "assets/stale.mp4" in report.missing_objects

    async with session_factory() as session:
        fresh_row = await session.get(Asset, fresh_id)
        assert fresh_row.status == "PENDING_UPLOAD"
        assert fresh_row.failed_at is None

        stale_row = await session.get(Asset, stale_id)
        assert stale_row.status == "FAILED"
        assert stale_row.failed_at is not None


@pytest.mark.asyncio
async def test_reconciler_promotion_vs_retention_race(session_factory, tmp_path):
    """Mandatory test: reconciler reads staging, retention claims asset,
    reconciler aborts without deleting another operation's object."""
    import asyncio
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    now = utcnow()

    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="IMAGE",
            role="PRODUCT_IMAGE",
            filename="race.png",
            content_type="image/png",
            object_key="assets/race.png",
            status="PENDING_UPLOAD",
            size_bytes=len(png_bytes()),
            checksum=hashlib.sha256(png_bytes()).hexdigest(),
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    staging_key = upload_staging_key(asset_id=asset_id)
    store.objects[staging_key] = (png_bytes(), "image/png")

    put_started = asyncio.Event()
    retention_done = asyncio.Event()

    original_put = store.put_bytes

    async def coordinated_put(key, data, content_type):
        result = await original_put(key, data, content_type)
        put_started.set()
        await retention_done.wait()
        return result

    store.put_bytes = coordinated_put

    reconciler = AssetReconciler(session_factory, store, settings(tmp_path))

    async def run_reconciler():
        snapshot = {
            "id": asset_id,
            "status": "PENDING_UPLOAD",
            "object_key": "assets/race.png",
            "checksum": hashlib.sha256(png_bytes()).hexdigest(),
            "size_bytes": len(png_bytes()),
            "content_type": "image/png",
            "filename": "race.png",
            "created_at": now - timedelta(hours=30),
        }
        report = ReconciliationReport()
        await reconciler._repair_pending(snapshot, report)
        return report

    async def run_retention():
        await put_started.wait()
        # Concurrent retention claims asset
        async with session_factory() as session, session.begin():
            row = await session.get(Asset, asset_id, with_for_update=True)
            row.status = "DELETING"
            row.delete_claimed_at = utcnow()
        retention_done.set()

    report, _ = await asyncio.gather(run_reconciler(), run_retention())

    assert report.compensation_failures == []
    assert await AssetRetentionService(
        session_factory, store, settings(tmp_path)
    ).finalize(asset_id, now=utcnow())

    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "DELETED"
        assert row.purged_at is not None
        assert row.operation_claim_id is None

    assert "assets/race.png" not in store.objects


@pytest.mark.asyncio
async def test_reconciler_nosuchbucket_keeps_ready_asset_unchanged(session_factory, tmp_path):
    """Mandatory test: READY asset with store returning NoSuchBucket
    remains READY, reported unavailable."""
    user_id, project_id = await seed_owner(session_factory)

    async with session_factory() as session, session.begin():
        asset = Asset(
            project_id=project_id,
            kind="VIDEO",
            role="FINAL_VIDEO",
            filename="ready.mp4",
            content_type="video/mp4",
            object_key="outputs/ready.mp4",
            status="READY",
            size_bytes=100,
            checksum="c" * 64,
            created_by=user_id,
        )
        session.add(asset)
        await session.flush()
        asset_id = asset.id

    class NoSuchBucketStore(FakeStore):
        async def head(self, key):
            raise AssetStoreUnavailableError("Storage bucket missing (NoSuchBucket)")

    reconciler = AssetReconciler(session_factory, NoSuchBucketStore(), settings(tmp_path))
    report = await reconciler.run(inspect_orphans=False)

    assert "outputs/ready.mp4" in report.unavailable_objects
    assert "outputs/ready.mp4" not in report.missing_objects
    assert "outputs/ready.mp4" not in report.corrupt_objects

    async with session_factory() as session:
        row = await session.get(Asset, asset_id)
        assert row.status == "READY"
