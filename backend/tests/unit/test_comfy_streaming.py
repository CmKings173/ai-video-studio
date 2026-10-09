import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from apps.api.app.integrations.comfy_adapter import ComfyAdapter, ComfyError


class OutputStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), error=None):
        self.chunks = chunks
        self.error = error
        self.consumed = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


def adapter_client(stream, headers=None, status=200, limit=4 * 1024**2):
    def respond(request):
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "remote.mp4"
        assert request.extensions["timeout"]["read"] == 30
        return httpx.Response(status, headers=headers, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True)
    adapter = ComfyAdapter(
        SimpleNamespace(comfyui_base_url="http://comfy.test", max_generated_output_bytes=limit),
        client=client,
    )
    return adapter, client


@pytest.mark.parametrize("declared", [False, True])
async def test_chunked_success_hashes_actual_file(tmp_path, declared):
    chunks = [b"a" * (1024**2 + 13), b"last"]
    data = b"".join(chunks)
    stream = OutputStream(chunks)
    headers = {"Content-Length": str(len(data))} if declared else {}
    adapter, client = adapter_client(stream, headers)
    path = tmp_path / "caller-chosen.bin"
    async with client:
        result = await adapter.download_to_path({"filename": "remote.mp4"}, str(path))
    assert result == {"path": path, "size": len(data), "checksum": hashlib.sha256(data).hexdigest()}
    assert path.read_bytes() == data
    assert stream.closed
    assert not (tmp_path / "remote.mp4").exists()


@pytest.mark.parametrize("length", ["9", "-1", "invalid", "1.5", "+3", "3, 3"])
async def test_bad_headers_rejected_before_body(tmp_path, length):
    stream = OutputStream([b"123"])
    adapter, client = adapter_client(stream, {"Content-Length": length}, limit=8)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert stream.consumed == 0
    assert stream.closed
    assert not path.exists()


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "2"}, {"Content-Length": "6"}])
async def test_runtime_limit_or_length_mismatch_removes_partial(tmp_path, headers):
    stream = OutputStream([b"12345"])
    adapter, client = adapter_client(stream, headers, limit=8)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError):
            await adapter.download_to_path({"filename": "remote.mp4"}, path, max_bytes=4)
    assert stream.closed
    assert not path.exists()


async def test_truncated_transfer_removes_partial(tmp_path):
    stream = OutputStream([b"123"], error=httpx.RemoteProtocolError("incomplete body"))
    adapter, client = adapter_client(stream, {"Content-Length": "4"})
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(httpx.RemoteProtocolError):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert stream.closed
    assert not path.exists()


@pytest.mark.parametrize("status", [302, 404, 500])
async def test_http_errors_and_redirects_do_not_read_body(tmp_path, status):
    stream = OutputStream([b"error"])
    adapter, client = adapter_client(stream, {"Location": "http://other.test/view"}, status)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(httpx.HTTPStatusError):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert stream.consumed == 0
    assert stream.closed
    assert not path.exists()


async def test_read_timeout_removes_partial(tmp_path):
    stream = OutputStream([b"a" * 1024**2], error=httpx.ReadTimeout("slow peer"))
    adapter, client = adapter_client(stream)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(httpx.ReadTimeout):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert stream.closed
    assert not path.exists()


async def test_cancellation_closes_stream_and_removes_written_file(tmp_path):
    waiting = asyncio.Event()

    class HeldStream(OutputStream):
        async def __aiter__(self):
            yield b"a" * 1024**2
            waiting.set()
            await asyncio.Event().wait()

    stream = HeldStream()
    adapter, client = adapter_client(stream)
    path = tmp_path / "partial"
    async with client:
        task = asyncio.create_task(adapter.download_to_path({"filename": "remote.mp4"}, path))
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            assert path.stat().st_size == 1024**2
        finally:
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed
    assert not path.exists()


async def test_existing_destination_is_preserved(tmp_path):
    stream = OutputStream([b"new"])
    adapter, client = adapter_client(stream)
    path = tmp_path / "existing"
    path.write_bytes(b"original")
    async with client:
        with pytest.raises(FileExistsError):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert path.read_bytes() == b"original"
    assert stream.closed


@pytest.mark.parametrize(
    "output",
    [
        {"filename": "../remote.mp4"},
        {"filename": "remote.mp4", "subfolder": "../escape"},
        {"filename": "remote.mp4", "type": "input"},
    ],
)
async def test_unsafe_output_never_creates_destination(tmp_path, output):
    stream = OutputStream([b"123"])
    adapter, client = adapter_client(stream)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError):
            await adapter.download_to_path(output, path)
    assert not path.exists()
    assert stream.consumed == 0


@pytest.mark.parametrize("length", ["2", "4"])
async def test_clean_eof_length_mismatch(tmp_path, length):
    stream = OutputStream([b"123"])
    adapter, client = adapter_client(stream, {"Content-Length": length})
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError, match="mismatch|truncated"):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert not path.exists()
    assert stream.closed


async def test_explicit_limit_cannot_raise_configured_ceiling(tmp_path):
    stream = OutputStream([b"12345"])
    adapter, client = adapter_client(stream, limit=4)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError) as error:
            await adapter.download_to_path({"filename": "remote.mp4"}, path, max_bytes=100)
    assert error.value.code == "COMFY_OUTPUT_TOO_LARGE"
    assert not path.exists()
    assert stream.closed


@pytest.mark.parametrize("limit", [0, -1])
async def test_nonpositive_limit_rejected(tmp_path, limit):
    stream = OutputStream([b"123"])
    adapter, client = adapter_client(stream)
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ValueError, match="positive"):
            await adapter.download_to_path({"filename": "remote.mp4"}, path, max_bytes=limit)
    assert stream.consumed == 0
    assert not path.exists()


async def test_unexpected_encoding_rejected_before_writing(tmp_path):
    stream = OutputStream([b"compressed"])
    adapter, client = adapter_client(stream, {"Content-Encoding": "gzip"})
    path = tmp_path / "partial"
    async with client:
        with pytest.raises(ComfyError, match="encoding"):
            await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert stream.consumed == 0
    assert stream.closed
    assert not path.exists()


async def test_overall_deadline_closes_stream_and_removes_partial(tmp_path):
    class HeldStream(OutputStream):
        async def __aiter__(self):
            yield b"a" * 1024**2
            await asyncio.Event().wait()

    stream = HeldStream()
    path = tmp_path / "partial"

    def respond(request):
        assert request.extensions["timeout"]["read"] == 0.05
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = ComfyAdapter("http://comfy.test", client=client)
        adapter.timeout = 0.05
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(adapter.download_to_path({"filename": "remote.mp4"}, path), 2)
    assert stream.closed
    assert not path.exists()


async def test_writes_never_exceed_one_mib(tmp_path, monkeypatch):
    data = b"a" * (3 * 1024**2 + 17)
    stream = OutputStream([data])
    adapter, client = adapter_client(stream)
    path = tmp_path / "output"
    real_open = Path.open
    sizes = []

    class RecordingFile:
        def __enter__(self):
            self.file = real_open(path, "xb")
            return self

        def write(self, chunk):
            sizes.append(len(chunk))
            return self.file.write(chunk)

        def __exit__(self, *args):
            self.file.close()

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: RecordingFile())
    async with client:
        result = await adapter.download_to_path({"filename": "remote.mp4"}, path)
    assert len(sizes) == 4
    assert max(sizes) <= 1024**2
    assert sum(sizes) == result["size"] == len(data)
    assert result["checksum"] == hashlib.sha256(data).hexdigest()
