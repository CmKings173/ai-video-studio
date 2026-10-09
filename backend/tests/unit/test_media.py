import asyncio

import pytest

from apps.api.app.integrations.media import MediaInspectionError, inspect_media


@pytest.mark.asyncio
async def test_missing_ffprobe_is_operational_not_media_corruption(monkeypatch):
    async def missing_binary(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", missing_binary)
    with pytest.raises(MediaInspectionError):
        await inspect_media(b"video-bytes", "video/mp4", "clip.mp4", "ffprobe")
