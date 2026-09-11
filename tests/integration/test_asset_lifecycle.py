from __future__ import annotations

import hashlib
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from PIL import Image

from apps.api.app.db.models import Asset, FinalVideo, Project, User, Video, utcnow
from apps.api.app.schemas.api import AssetComplete, UploadRequest
from apps.api.app.services.asset_service import (
    asset_is_referenced,
    complete_asset,
    create_pending_asset,
    upload_staging_key,
)
from workers.common import save_output
from workers.reconciliation import AssetReconciler


class FakeStore:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_count = 0

    async def presign_upload(self, key: str, content_type: str) -> dict:
        return {"url": "https://upload.invalid", "fields": {"key": key, "type": content_type}}

    async def put_bytes(self, key: str, data: bytes, content_type: str) -> dict:
        self.put_count += 1
        existing = self.objects.get(key)
        if existing is not None and existing != (data, content_type):
            raise RuntimeError("immutable conflict")
        self.objects[key] = (data, content_type)
        return {"key": key, "size": len(data), "checksum": hashlib.sha256(data).hexdigest()}

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
        self.objects.pop(key, None)


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
        assert await asset_is_referenced(session, asset.id)


@pytest.mark.asyncio
async def test_save_output_repairs_ready_database_row_when_object_is_missing(
    session_factory, tmp_path
):
    user_id, project_id = await seed_owner(session_factory)
    store = FakeStore()
    data = b"generated-video-bytes"
    metadata = {"width": 480, "height": 864, "duration_seconds": 5.0}

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
