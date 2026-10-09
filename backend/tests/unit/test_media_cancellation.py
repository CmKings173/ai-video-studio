"""File ownership through image inspection cancellation, including Windows cleanup."""

import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from PIL import Image

from apps.api.app.integrations.media import MediaValidationError, inspect_media_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cancellation", "inspection_error"),
    [("none", False), ("none", True), ("single", False), ("repeated", False),
     ("finish_race", False), ("single", True), ("repeated", True)],
)
async def test_inspection_owns_file_until_thread_finishes(
    tmp_path, monkeypatch, cancellation, inspection_error
):
    loop = asyncio.get_running_loop()
    opened = asyncio.Event()
    release, finished = threading.Event(), threading.Event()
    cleanup_states, cleanup_errors, handles = [], [], []
    prior_tasks = asyncio.all_tasks()
    directory = TemporaryDirectory(prefix="media-cancel-", dir=tmp_path)
    path = Path(directory.name) / "image.png"
    with Image.new("RGB", (8, 6)) as image:
        image.save(path)
    real_open = Image.open

    class HeldImage:
        def __enter__(self):
            self.image = real_open(path)
            handles.append(self.image.fp)
            self.width, self.height, self.format = (
                self.image.width, self.image.height, self.image.format
            )
            return self

        def verify(self):
            loop.call_soon_threadsafe(opened.set)
            assert release.wait(timeout=5), "test did not release inspection thread"
            if inspection_error:
                raise OSError("controlled inspection failure")
            self.image.verify()

        def __exit__(self, *args):
            self.image.close()
            finished.set()
            if cancellation == "finish_race":
                # Queue cancellation before to_thread can publish its result.
                loop.call_soon_threadsafe(task.cancel)

    monkeypatch.setattr(Image, "open", lambda *args, **kwargs: HeldImage())

    async def inspect_and_cleanup():
        try:
            return await inspect_media_path(path, "image/png")
        finally:
            cleanup_states.append((finished.is_set(), handles[0].closed))
            try:
                directory.cleanup()
            except OSError as exc:
                cleanup_errors.append(exc)
                raise

    async def settle_cancellation():
        # Yield through ready callbacks without relying on elapsed wall-clock time.
        for _ in range(12):
            await asyncio.sleep(0)

    task = asyncio.create_task(inspect_and_cleanup())
    try:
        await asyncio.wait_for(opened.wait(), timeout=5)
        assert not handles[0].closed
        if cancellation in {"single", "repeated"}:
            task.cancel("first")
            await settle_cancellation()
            assert not task.done()
            if cancellation == "repeated":
                for _ in range(3):
                    task.cancel("repeated")
                    await settle_cancellation()
                assert cleanup_states == [], (
                    "staging cleanup began while inspection still held its file",
                    cleanup_states,
                    cleanup_errors,
                )
            assert not task.done()
            assert not finished.is_set()
        release.set()
        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        if cancellation != "none":
            assert isinstance(outcome, asyncio.CancelledError)
            assert task.cancelled()
        elif inspection_error:
            assert isinstance(outcome, MediaValidationError)
            assert str(outcome) == "invalid image"
        else:
            assert outcome == {
                "kind": "IMAGE", "width": 8, "height": 6,
                "duration_seconds": None, "fps": None, "frames": 1,
                "content_type": "image/png",
            }
        assert cleanup_states == [(True, True)]
        assert cleanup_errors == []
        assert not path.parent.exists()
        assert not (asyncio.all_tasks() - prior_tasks)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 5)
        directory.cleanup()
