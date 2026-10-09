import asyncio
import threading

import pytest

from apps.api.app.integrations.ffmpeg import FFmpeg


@pytest.mark.asyncio
async def test_cancelled_encoder_relinquishes_output_before_cleanup(monkeypatch):
    started = asyncio.Event()

    class Process:
        returncode = None
        killed = False
        reaped = False

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            self.reaped = True
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(FFmpeg()._run("-i", "in.mp4", "out.mp4"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed
    assert process.reaped


@pytest.mark.asyncio
async def test_repeated_copy_cancellation_drains_file_before_temp_cleanup(tmp_path, monkeypatch):
    import apps.api.app.integrations.ffmpeg as module

    started, release, finished = (threading.Event() for _ in range(3))
    source_paths = []

    def held_copy(source, destination):
        source_paths.append(source)
        try:
            with open(source, "rb") as reader:
                started.set()
                assert release.wait(5)
                with open(destination, "wb") as writer:
                    writer.write(reader.read())
        finally:
            finished.set()

    encoder = FFmpeg()

    async def normalize(source, destination, *args):
        destination.write_bytes(b"clip")
        return {"duration_seconds": 1}

    async def concat(clips, output):
        output.write_bytes(b"assembled")

    monkeypatch.setattr(encoder, "_normalize", normalize)
    monkeypatch.setattr(encoder, "_concat_cut", concat)
    monkeypatch.setattr(module.shutil, "copyfile", held_copy)
    task = asyncio.create_task(
        encoder.assemble(
            [tmp_path / "input.mp4"],
            tmp_path / "output.mp4",
            {"width": 256, "height": 256, "fps": 24},
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert not finished.is_set()
        assert source_paths[0].exists()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert finished.is_set()
    assert not source_paths[0].exists()
