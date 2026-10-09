import asyncio
import threading

import pytest

from apps.api.app.integrations.minio import AssetStore


@pytest.mark.asyncio
async def test_repeated_transfer_cancellation_keeps_file_owned_until_thread_exits(tmp_path):
    started, release, finished = (threading.Event() for _ in range(3))
    source = tmp_path / "transfer.mp4"
    source.write_bytes(b"video")
    stop = threading.Event()

    def transfer():
        try:
            with source.open("rb") as reader:
                started.set()
                assert release.wait(5)
                return reader.read()
        finally:
            finished.set()

    task = asyncio.create_task(AssetStore._finish_thread(transfer, stop))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.02)
        assert stop.is_set()
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert not finished.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert finished.is_set()
    source.unlink()
