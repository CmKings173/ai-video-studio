import pytest

from apps.api.app.integrations import media


@pytest.mark.asyncio
async def test_stream_duration_is_separate_from_longer_container(monkeypatch, tmp_path):
    async def probe(*args):
        return {
            "format": {"duration": "12"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 480,
                    "height": 864,
                    "avg_frame_rate": "24/1",
                    "nb_frames": "72",
                    "duration": "3",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "duration_ts": "576000",
                    "time_base": "1/48000",
                },
            ],
        }

    monkeypatch.setattr(media, "_ffprobe", probe)
    value = await media.inspect_path(tmp_path / "synthetic.mp4")
    assert value["duration_seconds"] == 12
    assert value["video_duration_seconds"] == 3
    assert value["audio_duration_seconds"] == 12
    assert value["inspection_method"] == "ffprobe_declarations"
    assert "decoded_video_frames" not in value
