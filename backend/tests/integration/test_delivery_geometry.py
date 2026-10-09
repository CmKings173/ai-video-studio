"""Execute delivery geometry on CPU; no model/GPU qualification is implied."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.api.app.services.delivery_presets import resolve_delivery


def find_binaries():
    portable = (
        Path(__file__).resolve().parents[3]
        / "workspace/tools/ffmpeg-9.0.2/ffmpeg-9.0.2-essentials_build/bin"
    )
    ffmpeg = shutil.which("ffmpeg") or str(portable / "ffmpeg.exe")
    ffprobe = shutil.which("ffprobe") or str(portable / "ffprobe.exe")
    if not Path(ffmpeg).is_file() or not Path(ffprobe).is_file():
        pytest.skip("Real FFmpeg/ffprobe are required for CPU geometry verification")
    return ffmpeg, ffprobe


@pytest.fixture
def binaries():
    return find_binaries()


def run(*arguments):
    return subprocess.run(arguments, check=True, capture_output=True, timeout=60).stdout


@pytest.mark.parametrize("fit_mode", ["FIT_PAD", "CENTER_CROP"])
def test_delivery_geometry_measured_canvas_and_pixels(binaries, tmp_path, fit_mode):
    ffmpeg, ffprobe = binaries
    source = tmp_path / "source.mp4"
    output = tmp_path / "delivery.mp4"
    run(
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        "color=blue:size=320x180:rate=24:duration=1",
        "-vf",
        "drawbox=x=80:y=0:w=160:h=180:color=red:t=fill",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-y",
        str(source),
    )
    canvas = resolve_delivery(width=256, height=256, fps=25, fit_mode=fit_mode)
    run(
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(source),
        "-vf",
        canvas.video_filter(),
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        "-y",
        str(output),
    )
    streams = json.loads(run(ffprobe, "-v", "error", "-show_streams", "-of", "json", str(output)))[
        "streams"
    ]
    video = streams[0]
    assert (video["width"], video["height"]) == (256, 256)
    assert video["r_frame_rate"] == "25/1"
    assert video["sample_aspect_ratio"] == "1:1"
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"
    pixels = run(
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        str(output),
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    )
    assert len(pixels) == 256 * 256 * 3

    def pixel(x, y):
        start = (y * 256 + x) * 3
        return tuple(pixels[start : start + 3])

    top = pixel(128, 10)
    edge = pixel(32, 128)
    center = pixel(128, 128)
    assert center[0] > 200 and center[2] < 30
    if fit_mode == "FIT_PAD":
        assert max(top) < 10  # Letterbox, not stretch.
        assert edge[2] > 200 and edge[0] < 30  # Original blue margins remain.
    else:
        assert top[0] > 200 and top[2] < 30  # Center crop fills the canvas.
        assert edge[0] > 200 and edge[2] < 30  # Blue margins were cropped away.
