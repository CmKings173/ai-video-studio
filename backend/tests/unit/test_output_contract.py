import pytest

from apps.api.app.integrations.media import MediaValidationError
from workers.common import validate_generated_video


@pytest.mark.asyncio
async def test_generated_output_requires_video_stream(monkeypatch):
    async def inspect(_data, _content_type, _filename, _ffprobe_binary):
        return {"kind": "AUDIO", "has_video": False}

    monkeypatch.setattr("workers.common.inspect_media", inspect)

    with pytest.raises(MediaValidationError, match="video stream"):
        await validate_generated_video(b"audio", "audio.wav", "ffprobe")


@pytest.mark.asyncio
async def test_generated_output_rejects_non_video_comfy_kind(monkeypatch):
    async def inspect(_data, _content_type, _filename, _ffprobe_binary):
        return {"kind": "VIDEO", "has_video": True}

    monkeypatch.setattr("workers.common.inspect_media", inspect)

    with pytest.raises(MediaValidationError, match="output kind"):
        await validate_generated_video(b"gif", "animation.gif", "ffprobe", comfy_kind="gifs")


@pytest.mark.asyncio
async def test_generated_output_accepts_real_video(monkeypatch):
    async def inspect(_data, _content_type, _filename, _ffprobe_binary):
        return {"kind": "VIDEO", "has_video": True, "duration_seconds": 1.0}

    monkeypatch.setattr("workers.common.inspect_media", inspect)

    metadata = await validate_generated_video(b"video", "clip.mp4", "ffprobe", comfy_kind="videos")
    assert metadata["has_video"] is True


@pytest.mark.asyncio
async def test_save_output_rejects_metadata_without_video():
    from workers.common import save_output

    class Factory:
        def __call__(self):
            raise AssertionError("database should not be touched")

    with pytest.raises(ValueError, match="OUTPUT_NOT_VIDEO"):
        await save_output(
            Factory(),
            None,
            owner_id="x",
            role="GENERATED_VIDEO",
            project_id=None,
            created_by="u",
            data=b"x",
            metadata={"has_video": False},
        )

