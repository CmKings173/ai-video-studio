import hashlib
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest

from apps.api.app.core.config import Settings
from apps.api.app.integrations.comfy_adapter import ComfyAdapter, ComfyError
from apps.api.app.integrations.minio import AssetObjectTooLargeError, AssetStore, AssetStoreError


class StreamingClient:
    def __init__(self, data):
        self.data = data
        self.body = None
        self.puts = []

    def get_object(self, **kwargs):
        self.body = BytesIO(self.data)
        return {"Body": self.body, "ContentLength": len(self.data), "ContentType": "video/mp4"}

    def put_object(self, **kwargs):
        assert kwargs["IfNoneMatch"] == "*"
        body = kwargs["Body"]
        self.puts.append(body if isinstance(body, bytes) else body.read())


@pytest.mark.asyncio
async def test_generated_storage_limit_is_independent_of_upload_limit():
    client = StreamingClient(b"12345678")
    store = AssetStore(
        SimpleNamespace(max_upload_bytes=4, max_generated_output_bytes=12), client, client
    )
    assert await store.read("outputs/video", 12) == b"12345678"
    with pytest.raises(AssetStoreError):
        await store.read("staging/upload", 4)
    assert client.body.closed
    await store.put("outputs/video", b"12345678", "video/mp4")


@pytest.mark.asyncio
async def test_download_hashes_and_writes_bounded_file(tmp_path):
    client = StreamingClient(b"12345678")
    store = AssetStore(client=client, public_client=client)
    path = tmp_path / "video"
    value = await store.download_to_path("staging/upload", path, 8)
    assert path.read_bytes() == client.data
    assert value["checksum"] == hashlib.sha256(client.data).hexdigest()
    assert client.body.closed
    with pytest.raises(AssetObjectTooLargeError):
        await store.download_to_path("staging/upload", path, 4)
    assert not path.exists()


@pytest.mark.asyncio
async def test_file_promotion_is_conditional_and_streamed(tmp_path):
    client = StreamingClient(b"12345678")
    store = AssetStore(client=client, public_client=client)
    path = tmp_path / "video"
    path.write_bytes(client.data)
    digest = hashlib.sha256(client.data).hexdigest()
    result = await store.put_file_immutable("canonical/video", path, "video/mp4", digest, 8)
    assert result["checksum"] == digest
    assert client.puts == [client.data]
    with pytest.raises(AssetStoreError):
        await store.put_file_immutable("canonical/video", path, "video/mp4", digest, 4)
    assert client.puts == [client.data]


@pytest.mark.asyncio
async def test_immutable_conflict_verifies_actual_body_even_when_metadata_matches(tmp_path):
    from botocore.exceptions import ClientError

    expected = b"source-data"
    expected_digest = hashlib.sha256(expected).hexdigest()

    class StaleMetadataCollision(StreamingClient):
        def put_object(self, **kwargs):
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )

        def head_object(self, **kwargs):
            return {
                "ContentLength": len(expected),
                "ContentType": "video/mp4",
                "Metadata": {"sha256": expected_digest},
            }

    client = StaleMetadataCollision(b"X" * len(expected))
    path = tmp_path / "source.mp4"
    path.write_bytes(expected)
    store = AssetStore(client=client, public_client=client)
    with pytest.raises(AssetStoreError, match="Persisted immutable object bytes"):
        await store.put_file_immutable(
            "outputs/final/video.mp4", path, "video/mp4", expected_digest, len(expected)
        )


@pytest.mark.asyncio
async def test_comfy_output_limit_not_upload_limit():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345678"))
    ) as client:
        adapter = ComfyAdapter(
            SimpleNamespace(
                comfyui_base_url="http://comfy.test",
                max_upload_bytes=4,
                max_generated_output_bytes=8,
            ),
            client=client,
        )
        assert await adapter.download({"filename": "x.mp4"}) == b"12345678"
        adapter.max_download_bytes = 7
        with pytest.raises(ComfyError, match="size"):
            await adapter.download({"filename": "x.mp4"})


def test_limits_are_positive_and_independent():
    settings = Settings(_env_file=None, max_upload_bytes=1, max_generated_output_bytes=2)
    assert settings.max_generated_output_bytes == 2
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_generated_output_bytes=0)


@pytest.mark.asyncio
async def test_save_output_rejects_oversize_before_any_database_write():
    from workers.common import save_output

    def forbidden_factory():
        pytest.fail("Oversized output must be rejected before persistence")

    with pytest.raises(ValueError, match="OUTPUT_TOO_LARGE"):
        await save_output(
            forbidden_factory,
            object(),
            owner_id="test",
            role="GENERATED_VIDEO",
            project_id=None,
            created_by="test",
            data=b"12345",
            metadata={"kind": "VIDEO", "has_video": True},
            max_bytes=4,
        )


@pytest.mark.asyncio
async def test_persisted_checksum_streams_actual_bytes_and_closes_body():
    client = StreamingClient(b"12345678")
    store = AssetStore(client=client, public_client=client)
    actual = await store.checksum_object("outputs/video", 8)
    assert actual == {"size": 8, "checksum": hashlib.sha256(client.data).hexdigest()}
    assert client.body.closed
    with pytest.raises(AssetStoreError):
        await store.checksum_object("outputs/video", 7)
    assert client.body.closed


@pytest.mark.asyncio
async def test_truncated_stream_does_not_leave_temporary_file(tmp_path):
    from apps.api.app.integrations.minio import AssetStoreUnavailableError

    class Truncated(StreamingClient):
        def get_object(self, **kwargs):
            result = super().get_object(**kwargs)
            result["ContentLength"] += 1
            return result

    client = Truncated(b"1234567")
    path = tmp_path / "partial"
    with pytest.raises(AssetStoreUnavailableError, match="truncated"):
        await AssetStore(client=client, public_client=client).download_to_path("staging/x", path, 8)
    assert not path.exists()
    assert client.body.closed


@pytest.mark.asyncio
async def test_download_to_path_accepts_missing_content_length_but_enforces_actual_limit(tmp_path):
    class NoLength(StreamingClient):
        def get_object(self, **kwargs):
            self.body = BytesIO(self.data)
            return {"Body": self.body, "ContentType": "video/mp4"}

    client = NoLength(b"12345678")
    store = AssetStore(client=client, public_client=client)
    path = tmp_path / "bounded"
    result = await store.download_to_path("staging/x", path, 8)
    assert result == {
        "size": 8,
        "checksum": hashlib.sha256(client.data).hexdigest(),
        "content_type": "video/mp4",
    }
    assert path.read_bytes() == client.data
    assert client.body.closed

    with pytest.raises(AssetStoreError, match="size"):
        await store.download_to_path("staging/x", tmp_path / "too-large", 7)
    assert not (tmp_path / "too-large").exists()


@pytest.mark.asyncio
async def test_file_upload_streams_multipart_with_comfy_input_semantics(tmp_path, monkeypatch):
    data = b"x" * (2 * 1024 * 1024 + 17)
    path = tmp_path / "reference.mp4"
    path.write_bytes(data)
    observed = {}
    read_sizes = []
    real_open = type(path).open

    class TrackedFile:
        def __init__(self, file):
            self.file = file

        def read(self, size=-1):
            read_sizes.append(size)
            return self.file.read(size)

        def seek(self, *args):
            return self.file.seek(*args)

        def tell(self):
            return self.file.tell()

        def close(self):
            return self.file.close()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        type(path),
        "open",
        lambda self, *args, **kwargs: TrackedFile(real_open(self, *args, **kwargs)),
    )

    async def respond(request):
        observed["path"] = request.url.path
        body = await request.aread()
        observed["body"] = body
        observed["content_length"] = len(body)
        return httpx.Response(200, json={"name": "reference.mp4", "subfolder": "input"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = ComfyAdapter(
            SimpleNamespace(comfyui_base_url="http://comfy.test", max_upload_bytes=len(data)),
            client=client,
        )
        uploaded = await adapter.upload_file(path, "reference.mp4", "video/mp4")
        assert uploaded == "input/reference.mp4"

    assert observed["path"] == "/upload/image"
    assert b'name="type"\r\n\r\ninput' in observed["body"]
    assert b'name="overwrite"\r\n\r\nfalse' in observed["body"]
    assert b'filename="reference.mp4"' in observed["body"]
    assert data in observed["body"]
    assert read_sizes and all(0 < size <= 64 * 1024 for size in read_sizes)


@pytest.mark.asyncio
async def test_file_upload_rejects_oversize_before_comfy_request(tmp_path):
    path = tmp_path / "reference.mp4"
    path.write_bytes(b"12345")
    requests = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: requests.append(request))
    ) as client:
        adapter = ComfyAdapter(
            SimpleNamespace(comfyui_base_url="http://comfy.test", max_upload_bytes=4),
            client=client,
        )
        with pytest.raises(ComfyError, match="permitted size"):
            await adapter.upload_file(path, "reference.mp4", "video/mp4")
    assert requests == []


@pytest.mark.asyncio
async def test_file_upload_cancellation_closes_multipart_file_handle(tmp_path, monkeypatch):
    import asyncio

    path = tmp_path / "reference.mp4"
    path.write_bytes(b"reference")
    entered = asyncio.Event()
    blocked = asyncio.Event()
    real_open = type(path).open
    opened = []

    class TrackedFile:
        def __init__(self, file):
            self.file = file
            opened.append(self)

        @property
        def closed(self):
            return self.file.closed

        def read(self, size=-1):
            return self.file.read(size)

        def seek(self, *args):
            return self.file.seek(*args)

        def tell(self):
            return self.file.tell()

        def close(self):
            return self.file.close()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        type(path),
        "open",
        lambda self, *args, **kwargs: TrackedFile(real_open(self, *args, **kwargs)),
    )

    async def respond(_request):
        entered.set()
        await blocked.wait()
        return httpx.Response(200, json={"name": "reference.mp4"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = ComfyAdapter("http://comfy.test", client=client)
        task = asyncio.create_task(adapter.upload_file(path, "reference.mp4", "video/mp4"))
        await asyncio.wait_for(entered.wait(), 2)
        assert opened and not opened[0].closed
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert opened[0].closed


@pytest.mark.asyncio
async def test_conditional_file_put_enforces_transfer_deadline(tmp_path):
    import time

    from apps.api.app.integrations.minio import AssetStoreUnavailableError

    class SlowClient(StreamingClient):
        def put_object(self, **kwargs):
            time.sleep(0.02)
            kwargs["Body"].read(1)

    client = SlowClient(b"1234")
    path = tmp_path / "input"
    path.write_bytes(client.data)
    store = AssetStore(client=client, public_client=client)
    store.transfer_timeout = 0.01
    with pytest.raises(AssetStoreUnavailableError):
        await store.put_file_immutable(
            "assets/canonical", path, "video/mp4", hashlib.sha256(client.data).hexdigest(), 4
        )


@pytest.mark.asyncio
async def test_image_cancellation_drains_thread_before_file_cleanup(tmp_path, monkeypatch):
    import asyncio
    import threading

    from PIL import Image

    from apps.api.app.integrations.media import inspect_media_path

    path = tmp_path / "image.png"
    with Image.new("RGB", (8, 6)) as image:
        image.save(path)
    opened, release, closed = threading.Event(), threading.Event(), threading.Event()
    real_open = Image.open

    class HeldImage:
        def __enter__(self):
            self.image = real_open(path)
            self.width, self.height, self.format = (
                self.image.width,
                self.image.height,
                self.image.format,
            )
            return self

        def verify(self):
            opened.set()
            assert release.wait(timeout=5)
            self.image.verify()

        def __exit__(self, *args):
            self.image.close()
            closed.set()

    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: HeldImage())
    task = asyncio.create_task(inspect_media_path(path, "image/png"))
    try:
        assert await asyncio.to_thread(opened.wait, 5)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not closed.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
