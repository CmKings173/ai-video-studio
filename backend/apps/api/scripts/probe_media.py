"""Measure downloaded PoC output without inventing runtime provenance."""

from __future__ import annotations

import asyncio
import hashlib
import math
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from apps.api.app.integrations.media import MediaInspectionError, MediaValidationError, inspect_path


class ProbeOutputError(ValueError):
    """Downloaded media does not satisfy the submitted output contract."""


def _positive_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _framehash_evidence(output: bytes) -> dict[str, Any]:
    """Count decoded raw-video/PCM packets and their actual timestamp spans."""
    timebases, media_types = {}, {}
    counts, starts, ends, previous = {}, {}, {}, {}
    try:
        for line in output.decode("utf-8").splitlines():
            if line.startswith("#tb ") or line.startswith("#media_type "):
                key, value = line.split(":", 1)
                index = int(key.split()[-1])
                if key.startswith("#tb "):
                    timebases[index] = Fraction(value.strip())
                    if timebases[index] <= 0:
                        raise ValueError("Invalid decoder time base")
                else:
                    media_types[index] = value.strip()
            elif line and not line.startswith("#"):
                parts = line.split(",")
                index, _, pts, duration, size = (int(value) for value in parts[:5])
                if len(parts) != 6 or duration <= 0 or size <= 0:
                    raise ValueError("Invalid decoded packet")
                start = float(pts * timebases[index])
                end = float((pts + duration) * timebases[index])
                if index in previous and start <= previous[index]:
                    raise ProbeOutputError("Non-increasing decoded stream timestamps")
                previous[index] = start
                counts[index] = counts.get(index, 0) + 1
                starts[index] = min(starts.get(index, start), start)
                ends[index] = max(ends.get(index, end), end)
        evidence = {}
        for index, kind in ((0, "video"), (1, "audio")):
            if media_types.get(index) != kind:
                raise ValueError("Decoder stream mapping is unavailable")
            count = counts.get(index, 0)
            if not count:
                raise ProbeOutputError(f"No decoded {kind} frames")
            evidence[f"decoded_{kind}_frames"] = count
            evidence[f"decoded_{kind}_duration_seconds"] = ends[index] - starts[index]
            evidence[f"decoded_{kind}_start_seconds"] = starts[index]
        return evidence
    except (ValueError, KeyError, UnicodeError, ZeroDivisionError) as exc:
        if isinstance(exc, ProbeOutputError):
            raise
        raise MediaInspectionError("FFmpeg returned invalid decode evidence") from exc


async def _decode_output(path: Path, binary: str, timeout_seconds: float) -> dict[str, Any]:
    # Rawvideo and PCM require actual decoding of BOTH selected streams. Passthrough
    # preserves missing/gapped video frames instead of manufacturing CFR duplicates.
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "-v",
            "error",
            "-nostdin",
            "-xerror",
            "-err_detect",
            "explode",
            "-hwaccel",
            "none",
            "-threads",
            "1",
            "-copyts",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c:v",
            "rawvideo",
            "-threads:v",
            "1",
            "-fps_mode",
            "passthrough",
            "-c:a",
            "pcm_s16le",
            "-f",
            "framehash",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise MediaInspectionError("FFmpeg decoder is unavailable") from exc

    async def read_bounded(stream, limit):
        data = bytearray()
        while chunk := await stream.read(min(65536, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise MediaInspectionError("FFmpeg decode evidence exceeds the output bound")
        return bytes(data)

    tasks = [
        asyncio.create_task(read_bounded(process.stdout, 2 * 1024 * 1024)),
        asyncio.create_task(read_bounded(process.stderr, 64 * 1024)),
        asyncio.create_task(process.wait()),
    ]
    try:
        stdout, stderr, returncode = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        raise MediaInspectionError("FFmpeg decode timed out") from exc
    finally:
        if process.returncode is None:
            process.kill()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # Drain remaining pipe buffers after terminating a bounded/cancelled run;
        # wait() alone can deadlock on Windows when stdout was backpressured.
        await process.communicate()
    if returncode != 0:
        diagnostic = stderr.decode("utf-8", errors="replace")[-500:]
        # Decoder input errors are invalid media. Startup/configuration/encoder
        # failures remain tooling errors and must never mark media corrupt.
        invalid = (
            "invalid data",
            "error while decoding",
            "error submitting packet",
            "corrupt",
            "invalid nal",
            "invalid packet",
            "error decoding",
        )
        if any(message in stderr.decode("utf-8", errors="replace").lower() for message in invalid):
            raise ProbeOutputError(f"AV decode failed: {diagnostic}")
        raise MediaInspectionError(f"FFmpeg decode tool failed: {diagnostic}")
    return _framehash_evidence(stdout)


async def measure_probe_output(
    data: bytes,
    *,
    expected_width: int,
    expected_height: int,
    expected_fps: float,
    expected_frames: int,
    ffprobe_binary: str = "ffprobe",
    ffmpeg_binary: str | None = None,
    decode_timeout_seconds: float = 30,
) -> dict[str, Any]:
    """Hash and inspect actual bytes, then compare to the resolved submitted graph.

    Decoded count and video span must match the graph. Audio/container padding
    may differ by one frame. Missing declared frame count stays missing.
    Tool failures propagate separately from confirmed invalid output. This helper
    alone cannot qualify a workflow: callers must bind runtime and graph evidence.
    """
    if not data:
        raise ProbeOutputError("ComfyUI returned empty output bytes")
    if len(data) > 512 * 1024 * 1024:
        raise ProbeOutputError("Downloaded output exceeds the byte bound")
    if (
        type(expected_width) is not int
        or expected_width <= 0
        or type(expected_height) is not int
        or expected_height <= 0
        or type(expected_frames) is not int
        or expected_frames <= 0
        or not _positive_number(expected_fps)
        or not _positive_number(decode_timeout_seconds)
    ):
        raise ProbeOutputError("Submitted output contract is invalid")
    with TemporaryDirectory(prefix="studio-poc-output-") as directory:
        path = Path(directory) / "output.mp4"
        await asyncio.to_thread(path.write_bytes, data)
        try:
            measured = await inspect_path(path, ffprobe_binary)
        except MediaValidationError as exc:
            raise ProbeOutputError(str(exc)) from exc
        _validate_declarations(
            measured, expected_width, expected_height, expected_fps, expected_frames
        )
        probe_path = Path(ffprobe_binary)
        decoder = ffmpeg_binary or str(
            probe_path.with_name("ffmpeg.exe" if probe_path.suffix.lower() == ".exe" else "ffmpeg")
        )
        decoded = await _decode_output(path, decoder, decode_timeout_seconds)
    if decoded["decoded_video_frames"] != expected_frames:
        raise ProbeOutputError("Actual decoded video frame count differs from submitted graph")
    expected_duration = expected_frames / expected_fps
    video_duration = decoded["decoded_video_duration_seconds"]
    if abs(video_duration - expected_duration) > 0.001:
        raise ProbeOutputError("Actual decoded video duration differs from submitted graph")
    if abs(decoded["decoded_audio_duration_seconds"] - video_duration) > 1 / expected_fps + 0.001:
        raise ProbeOutputError("Actual decoded audio duration differs from video duration")
    if abs(decoded["decoded_audio_start_seconds"] - decoded["decoded_video_start_seconds"]) > (
        1 / expected_fps + 0.001
    ):
        raise ProbeOutputError("Actual decoded AV start times differ")
    return {
        **measured,
        **decoded,
        "inspection_method": "ffprobe_declarations+ffmpeg_full_av_decode",
        "checksum": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def _validate_declarations(
    measured, expected_width, expected_height, expected_fps, expected_frames
):
    if measured.get("has_video") is not True or measured.get("has_audio") is not True:
        raise ProbeOutputError("H3 output must contain video and audio streams")
    for name, expected in (("width", expected_width), ("height", expected_height)):
        actual = measured.get(name)
        if type(actual) is not int or actual != expected:
            raise ProbeOutputError(f"Measured output {name} differs from submitted graph")
    actual_fps = measured.get("fps")
    duration = measured.get("duration_seconds")
    if not _positive_number(actual_fps) or abs(actual_fps - expected_fps) > 0.001:
        raise ProbeOutputError("Measured output FPS differs from submitted graph")
    if not _positive_number(duration):
        raise ProbeOutputError("Measured output duration is invalid")
    expected_duration = expected_frames / expected_fps
    if abs(duration - expected_duration) > 1 / expected_fps + 0.001:
        raise ProbeOutputError("Measured output duration differs from submitted graph")
    frames = measured.get("frames")
    if frames is not None and (type(frames) is not int or frames != expected_frames):
        raise ProbeOutputError("Measured output frame count differs from submitted graph")
