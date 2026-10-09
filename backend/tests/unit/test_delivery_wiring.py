from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.api.app.integrations.ffmpeg import FFmpeg
from apps.api.app.schemas.api import AssemblyRequest


def test_preset_request_accepts_ultrawide_and_rejects_ignored_overrides():
    request = AssemblyRequest(delivery_preset="ULTRAWIDE_2560_1080", fit_mode="CENTER_CROP")
    assert request.width is None
    with pytest.raises(ValidationError):
        AssemblyRequest(delivery_preset="ULTRAWIDE_2560_1080", width=1920)
    with pytest.raises(ValidationError):
        AssemblyRequest(width=4096, height=4096)


@pytest.mark.asyncio
async def test_assembly_forwards_resolved_preset_and_fit_mode(tmp_path, monkeypatch):
    ffmpeg = FFmpeg()
    received = []

    async def normalize(source, destination, width, height, fps, fit_mode):
        received.append((width, height, fps, fit_mode))
        destination.write_bytes(b"normalized")
        return {"duration_seconds": 1}

    async def concat(clips, output):
        output.write_bytes(b"assembled")

    async def probe(path):
        return {"has_video": True}

    monkeypatch.setattr(ffmpeg, "_normalize", normalize)
    monkeypatch.setattr(ffmpeg, "_concat_cut", concat)
    monkeypatch.setattr(ffmpeg, "probe", probe)
    await ffmpeg.assemble(
        [Path("source.mp4")],
        tmp_path / "final.mp4",
        {
            "delivery_preset": "ULTRAWIDE_2560_1080",
            "fit_mode": "CENTER_CROP",
            "fps": 25,
        },
    )
    assert received == [(2560, 1080, 25, "CENTER_CROP")]


@pytest.mark.asyncio
async def test_muted_delivery_keeps_aac_stream(tmp_path, monkeypatch):
    ffmpeg = FFmpeg()
    commands = []

    async def normalize(source, destination, *geometry):
        destination.write_bytes(b"normalized")
        return {"duration_seconds": 1}

    async def concat(clips, output):
        output.write_bytes(b"assembled")

    async def execute(*arguments):
        commands.append(arguments)

    async def probe(path):
        return {"has_video": True, "has_audio": True, "audio_codec": "aac"}

    monkeypatch.setattr(ffmpeg, "_normalize", normalize)
    monkeypatch.setattr(ffmpeg, "_concat_cut", concat)
    monkeypatch.setattr(ffmpeg, "_run", execute)
    monkeypatch.setattr(ffmpeg, "probe", probe)
    result = await ffmpeg.assemble(
        [Path("source.mp4")], tmp_path / "muted.mp4", {"audio_mode": "MUTE_SCENE_AUDIO"}
    )
    assert result["has_audio"] and result["audio_codec"] == "aac"
    assert "-an" not in commands[0]
    assert "volume=0" in commands[0]
