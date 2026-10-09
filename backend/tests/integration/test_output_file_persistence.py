from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select

from apps.api.app.db.models import Asset
from apps.api.app.integrations.minio import AssetStoreError
from tests.integration.test_asset_claims import seed_asset_owner
from workers import common


class FileStore:
    def __init__(self):
        self.objects = {}
        self.uploads = 0
        self.on_upload = None
        self.on_verify = None
        self.deleted = []

    async def put_file_immutable(self, key, path, content_type, checksum, max_bytes):
        self.uploads += 1
        if self.on_upload:
            await self.on_upload()
        with path.open("rb") as source:
            data = b"".join(iter(lambda: source.read(1024 * 1024), b""))
        assert len(data) <= max_bytes
        if key in self.objects and self.objects[key] != data:
            raise AssetStoreError("Immutable object key already contains different data")
        self.objects[key] = data

    async def checksum_object(self, key, max_bytes):
        data = self.objects[key]
        if self.on_verify:
            await self.on_verify()
        assert len(data) <= max_bytes
        return {"size": len(data), "checksum": hashlib.sha256(data).hexdigest()}

    async def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def put_bytes(self, *args):
        raise AssertionError("file persistence must never call put_bytes")


async def prepared(factory, tmp_path, store=None):
    user_id, project_id = await seed_asset_owner(factory)
    path = tmp_path / "output.mp4"
    path.write_bytes(b"video-payload" * 1000)
    return store or FileStore(), dict(
        owner_id="file-owner",
        role="GENERATED_VIDEO",
        project_id=project_id,
        created_by=user_id,
        path=path,
        metadata={"kind": "VIDEO", "has_video": True, "width": 16, "height": 9},
    )


@pytest.mark.asyncio
async def test_file_upload_digest_size_and_ready_replay(session_factory, tmp_path, monkeypatch):
    store, args = await prepared(session_factory, tmp_path)
    monkeypatch.setattr(type(args["path"]), "read_bytes", lambda self: pytest.fail("whole read"))
    asset_id = await common.save_output_file(session_factory, store, **args)
    assert await common.save_output_file(session_factory, store, **args) == asset_id
    assert store.uploads == 1
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.status == "READY"
        assert asset.size_bytes == args["path"].stat().st_size
        assert asset.checksum == hashlib.sha256(store.objects[asset.object_key]).hexdigest()
        assert asset.media_metadata["checksum"] == asset.checksum
        assert asset.operation_claim_id is None


@pytest.mark.asyncio
async def test_ready_replay_verifies_bytes_and_releases_claim(session_factory, tmp_path):
    store, args = await prepared(session_factory, tmp_path)
    asset_id = await common.save_output_file(session_factory, store, **args)
    async with session_factory() as session:
        key = (await session.get(Asset, asset_id)).object_key
    store.objects[key] = b"different"
    with pytest.raises(ValueError, match="ASSET_CHECKSUM_MISMATCH"):
        await common.save_output_file(session_factory, store, **args)
    assert store.uploads == 1
    async with session_factory() as session:
        assert (await session.get(Asset, asset_id)).operation_claim_id is None


@pytest.mark.asyncio
async def test_file_immutable_collision_never_publishes_ready(session_factory, tmp_path):
    store, args = await prepared(session_factory, tmp_path)
    digest = hashlib.sha256(b"video-payload" * 1000).hexdigest()
    key = f"outputs/generated_video/file-owner/{digest}.mp4"
    store.objects[key] = b"foreign-bytes"
    with pytest.raises(AssetStoreError, match="Immutable"):
        await common.save_output_file(session_factory, store, **args)
    async with session_factory() as session:
        asset = await session.scalar(select(Asset))
        assert asset.status == "PENDING_UPLOAD"
        assert asset.operation_claim_id is None
    assert store.objects[key] == b"foreign-bytes"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["before", "during", "after"])
async def test_file_claim_loss_never_ready(session_factory, tmp_path, monkeypatch, phase):
    store, args = await prepared(session_factory, tmp_path)

    async def replace_claim():
        async with session_factory() as session, session.begin():
            asset = await session.scalar(select(Asset))
            asset.operation_claim_id = str(uuid4())

    if phase == "before":
        original = common.claim_heartbeat

        @asynccontextmanager
        async def lose_before(*args, **kwargs):
            async with original(*args, **kwargs) as lost:
                await replace_claim()
                yield lost

        monkeypatch.setattr(common, "claim_heartbeat", lose_before)
    elif phase == "during":
        store.on_upload = replace_claim
    else:
        store.on_verify = replace_claim
    with pytest.raises(ValueError, match="ASSET_OPERATION_BUSY"):
        await common.save_output_file(session_factory, store, **args)
    async with session_factory() as session:
        asset = await session.scalar(select(Asset))
        assert asset.status == "PENDING_UPLOAD"
        assert asset.operation_claim_id is not None  # replacement owner survives
    assert store.uploads == (0 if phase == "before" else 1)
    assert store.deleted == []  # never remove replacement-owned bytes


@pytest.mark.asyncio
async def test_file_delete_race_compensates_without_ready(session_factory, tmp_path):
    store, args = await prepared(session_factory, tmp_path)

    async def delete_during_upload():
        async with session_factory() as session, session.begin():
            asset = await session.scalar(select(Asset))
            asset.status = "DELETING"
            asset.operation_claim_id = None
            asset.operation_claim_type = None

    store.on_upload = delete_during_upload
    with pytest.raises(ValueError, match="ASSET_OPERATION_BUSY"):
        await common.save_output_file(session_factory, store, **args)
    assert len(store.deleted) == 1
    assert not store.objects


@pytest.mark.asyncio
async def test_file_cancel_releases_claim(session_factory, tmp_path):
    store, args = await prepared(session_factory, tmp_path)
    started = asyncio.Event()

    async def wait_upload():
        started.set()
        await asyncio.Event().wait()

    store.on_upload = wait_upload
    task = asyncio.create_task(common.save_output_file(session_factory, store, **args))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with session_factory() as session:
        asset = await session.scalar(select(Asset))
        assert asset.status == "PENDING_UPLOAD"
        assert asset.operation_claim_id is None


@pytest.mark.asyncio
async def test_file_limit_rejects_before_db(tmp_path):
    path = tmp_path / "large.mp4"
    path.write_bytes(b"oversize")

    def no_db():
        pytest.fail("oversize must be rejected before DB intent")

    with pytest.raises(ValueError, match="OUTPUT_TOO_LARGE"):
        await common.save_output_file(
            no_db,
            FileStore(),
            path=path,
            owner_id="x",
            role="FINAL_VIDEO",
            project_id=None,
            created_by="x",
            metadata={"kind": "VIDEO", "has_video": True},
            max_bytes=2,
        )


@pytest.mark.asyncio
async def test_file_ready_missing_object_is_repaired(session_factory, tmp_path):
    store, args = await prepared(session_factory, tmp_path)
    asset_id = await common.save_output_file(session_factory, store, **args)
    store.objects.clear()
    assert await common.save_output_file(session_factory, store, **args) == asset_id
    assert store.uploads == 2
    async with session_factory() as session:
        asset = await session.get(Asset, asset_id)
        assert asset.status == "READY"
        assert asset.operation_claim_id is None


@pytest.mark.asyncio
async def test_file_publication_db_error_releases_claim(session_factory, tmp_path):
    from sqlalchemy.exc import OperationalError

    store, args = await prepared(session_factory, tmp_path)

    class FailedSession:
        async def __aenter__(self):
            raise OperationalError("publish output", {}, RuntimeError("DB failed"))

        async def __aexit__(self, *args):
            return False

    class FailPublication:
        failing = False

        def __call__(self):
            if self.failing:
                self.failing = False
                return FailedSession()
            return session_factory()

    factory = FailPublication()

    async def fail_next_session():
        factory.failing = True

    store.on_verify = fail_next_session
    with pytest.raises(OperationalError):
        await common.save_output_file(factory, store, **args)
    async with session_factory() as session:
        asset = await session.scalar(select(Asset))
        assert asset.status == "PENDING_UPLOAD"
        assert asset.operation_claim_id is None
    assert len(store.objects) == 1  # pending replay can recover verified immutable bytes
