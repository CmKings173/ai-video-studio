import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from apps.api.app.core.errors import AppError
from apps.api.app.db.models import Asset
from tests.unit.test_director_execution_contract import candidate
from workers import dispatcher as dispatcher_module
from workers.dispatcher import Dispatcher


class StreamingStore:
    def __init__(self, content):
        self.content = content
        self.path = None
        self.calls = []

    async def download_to_path(self, key, path, max_bytes):
        self.calls.append((key, max_bytes))
        self.path = path
        digest = hashlib.sha256()
        size = 0
        with path.open("wb") as output:
            for offset in range(0, len(self.content), 3):
                chunk = self.content[offset : offset + 3]
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        return {"size": size, "checksum": digest.hexdigest(), "content_type": "video/mp4"}

    async def get_bytes(self, _key):
        raise AssertionError("Reference staging must not buffer the object")


class FileAdapter:
    def __init__(self, *, fail=False):
        self.uploads = []
        self.fail = fail

    async def upload_file(self, path, filename, content_type):
        assert path.is_file()
        self.uploads.append((path, filename, content_type, path.is_file(), path.read_bytes()))
        if self.fail:
            raise RuntimeError("Comfy unavailable")
        return {"name": filename, "subfolder": "input"}

    async def object_info(self):
        return {}


def test_dispatcher_downloads_reference_to_disk_verifies_and_uploads_before_cleanup(
    tmp_path, monkeypatch
):
    content = b"reference video bytes"
    checksum = hashlib.sha256(content).hexdigest()
    store = StreamingStore(content)
    adapter = FileAdapter()
    asset_id = "asset-1"
    asset_record = SimpleNamespace(
        id=asset_id,
        status="READY",
        deleted_at=None,
        object_key="canonical/reference",
        checksum=checksum,
        size_bytes=len(content),
    )

    @asynccontextmanager
    async def session():
        class FakeSession:
            async def get(self, model, key):
                assert model is Asset
                assert key == asset_id
                return asset_record

        yield FakeSession()

    built = {}

    class Builder:
        def build(self, **kwargs):
            built.update(kwargs)
            return {"graph": "ready"}

    monkeypatch.setattr(dispatcher_module, "DirectorWorkflowBuilder", Builder)
    settings = SimpleNamespace(
        workspace_root=tmp_path,
        min_free_disk_bytes=0,
        max_upload_bytes=1024,
    )
    dispatcher = Dispatcher(lambda: session(), adapter, store, None, settings)
    monkeypatch.setattr(dispatcher, "_require_execution_snapshot", _skip_snapshot_check)
    spec, graph = candidate()
    snapshot = {
        "director_execution": spec.model_dump(mode="json"),
        "base_workflow": graph,
        "assets": [
            {
                "id": asset_id,
                "object_key": "canonical/reference",
                "checksum": checksum,
                "size_bytes": len(content),
                "filename": "reference.mp4",
                "content_type": "video/mp4",
                "role": "REFERENCE_VIDEO",
                "order_index": 0,
            }
        ],
    }

    assert asyncio.run(dispatcher._prepare_workflow(snapshot)) == {"graph": "ready"}
    assert store.calls == [("canonical/reference", len(content))]
    expected_name = f"{spec.execution_hash[:24]}_reference_video_0_{checksum[:12]}.mp4"
    assert adapter.uploads[0][1:] == (expected_name, "video/mp4", True, content)
    assert not adapter.uploads[0][0].exists()
    assert not store.path.exists()
    assert built["staged_assets"] == {
        ("REFERENCE_VIDEO", 0): f"input/{adapter.uploads[0][1]}"
    }


async def _skip_snapshot_check(_snapshot):
    return None


@pytest.mark.parametrize("damage", ["checksum", "size", "limit", "truncated"])
def test_dispatcher_fails_closed_and_cleans_reference_temp_file(tmp_path, monkeypatch, damage):
    content = b"reference video bytes"
    checksum = hashlib.sha256(content).hexdigest()
    store = StreamingStore(content)
    adapter = FileAdapter()
    database_size = len(content) + 1 if damage == "truncated" else len(content)
    asset_record = SimpleNamespace(
        id="asset-1",
        status="READY",
        deleted_at=None,
        object_key="canonical/reference",
        checksum=checksum,
        size_bytes=database_size,
    )

    @asynccontextmanager
    async def session():
        class FakeSession:
            async def get(self, _model, _key):
                return asset_record

        yield FakeSession()

    class Builder:
        def build(self, **kwargs):
            return kwargs

    monkeypatch.setattr(dispatcher_module, "DirectorWorkflowBuilder", Builder)
    max_upload_bytes = 5 if damage == "limit" else 1024
    dispatcher = Dispatcher(
        lambda: session(),
        adapter,
        store,
        None,
        SimpleNamespace(
            workspace_root=tmp_path,
            min_free_disk_bytes=0,
            max_upload_bytes=max_upload_bytes,
        ),
    )
    monkeypatch.setattr(dispatcher, "_require_execution_snapshot", _skip_snapshot_check)
    spec, graph = candidate()
    frozen_checksum = "0" * 64 if damage == "checksum" else checksum
    expected_size = len(content) + 1 if damage in {"size", "truncated"} else len(content)
    snapshot = {
        "director_execution": spec.model_dump(mode="json"),
        "base_workflow": graph,
        "assets": [
            {
                "id": "asset-1",
                "object_key": "canonical/reference",
                "checksum": frozen_checksum,
                "size_bytes": expected_size,
                "filename": "reference.mp4",
                "content_type": "video/mp4",
                "role": "REFERENCE_VIDEO",
                "order_index": 0,
            }
        ],
    }

    with pytest.raises((ValueError, AppError)):
        asyncio.run(dispatcher._prepare_workflow(snapshot))
    assert adapter.uploads == []
    if store.path:
        assert not store.path.exists()


def test_dispatcher_does_not_upload_reference_after_comfy_failure(tmp_path, monkeypatch):
    content = b"reference video bytes"
    checksum = hashlib.sha256(content).hexdigest()
    store = StreamingStore(content)
    adapter = FileAdapter(fail=True)
    asset_record = SimpleNamespace(
        id="asset-1",
        status="READY",
        deleted_at=None,
        object_key="canonical/reference",
        checksum=checksum,
        size_bytes=len(content),
    )

    @asynccontextmanager
    async def session():
        class FakeSession:
            async def get(self, _model, _key):
                return asset_record

        yield FakeSession()

    monkeypatch.setattr(
        dispatcher_module,
        "DirectorWorkflowBuilder",
        lambda: SimpleNamespace(build=lambda **kwargs: kwargs),
    )
    dispatcher = Dispatcher(
        lambda: session(),
        adapter,
        store,
        None,
        SimpleNamespace(workspace_root=tmp_path, min_free_disk_bytes=0, max_upload_bytes=1024),
    )
    monkeypatch.setattr(dispatcher, "_require_execution_snapshot", _skip_snapshot_check)
    spec, graph = candidate()
    snapshot = {
        "director_execution": spec.model_dump(mode="json"),
        "base_workflow": graph,
        "assets": [
            {
                "id": "asset-1",
                "object_key": "canonical/reference",
                "checksum": checksum,
                "size_bytes": len(content),
                "filename": "reference.mp4",
                "content_type": "video/mp4",
                "role": "REFERENCE_VIDEO",
                "order_index": 0,
            }
        ],
    }
    with pytest.raises(RuntimeError, match="Comfy unavailable"):
        asyncio.run(dispatcher._prepare_workflow(snapshot))
    assert adapter.uploads[0][3] is True
    assert not store.path.exists()
