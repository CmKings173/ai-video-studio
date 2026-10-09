"""Bounded media inspection for uploaded and generated assets."""

from __future__ import annotations

import asyncio
import json
import math
import mimetypes
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, UnidentifiedImageError


class MediaValidationError(ValueError):
    """The bytes are confirmed invalid for the declared media contract."""


class MediaInspectionError(RuntimeError):
    """Media tooling could not inspect the bytes; retry without marking corrupt."""


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/webm", "video/quicktime"}
AUDIO_TYPES = {"audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"}
ALLOWED_TYPES = IMAGE_TYPES | VIDEO_TYPES | AUDIO_TYPES


def kind_for_content_type(content_type: str) -> str:
    if content_type in IMAGE_TYPES:
        return "IMAGE"
    if content_type in VIDEO_TYPES:
        return "VIDEO"
    if content_type in AUDIO_TYPES:
        return "AUDIO"
    raise MediaValidationError("unsupported content type")


async def _ffprobe(path: Path, binary: str, timeout_seconds: int = 30) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        raise MediaInspectionError("media inspection tool is unavailable") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.CancelledError:
        process.kill()
        await process.wait()
        raise
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise MediaInspectionError("media inspection timed out") from exc
    if process.returncode != 0:
        raise MediaValidationError(
            f"invalid media: {stderr.decode('utf-8', errors='replace')[-500:]}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MediaInspectionError("ffprobe returned invalid metadata") from exc


async def inspect_path(path: Path, ffprobe_binary: str = "ffprobe") -> dict[str, Any]:
    value = await _ffprobe(path, ffprobe_binary)
    streams = value.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration_value = (value.get("format") or {}).get("duration")
    duration = stream_duration({"duration": duration_value})
    fps = None
    frames = None
    if video:
        fraction = video.get("avg_frame_rate") or video.get("r_frame_rate")
        if fraction and fraction != "0/0":
            numerator, denominator = fraction.split("/", 1)
            if float(denominator):
                fps = float(numerator) / float(denominator)
        if video.get("nb_frames") not in (None, "N/A"):
            frames = int(video["nb_frames"])
    if not video and not audio:
        raise MediaValidationError("media has no audio or video stream")
    return {
        "kind": "VIDEO" if video else "AUDIO",
        "width": int(video["width"]) if video and video.get("width") else None,
        "height": int(video["height"]) if video and video.get("height") else None,
        "duration_seconds": duration,
        "video_duration_seconds": stream_duration(video),
        "audio_duration_seconds": stream_duration(audio),
        "inspection_method": "ffprobe_declarations",
        "fps": fps,
        "frames": frames,
        "frame_count": frames,
        "has_audio": bool(audio),
        "has_video": bool(video),
        "codec": video.get("codec_name") if video else audio.get("codec_name"),
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_sample_rate": _positive_int(audio.get("sample_rate")) if audio else None,
        "audio_channels": _positive_int(audio.get("channels")) if audio else None,
        "container_format": (value.get("format") or {}).get("format_name"),
    }


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def stream_duration(stream: dict | None) -> float | None:
    """Keep declared stream span distinct from container span and decode evidence."""
    if not stream:
        return None
    try:
        if stream.get("duration") not in (None, "N/A"):
            duration = float(stream["duration"])
        elif stream.get("duration_ts") not in (None, "N/A") and stream.get("time_base"):
            duration = float(stream["duration_ts"]) * float(Fraction(stream["time_base"]))
        else:
            return None
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


async def inspect_media(
    data: bytes,
    content_type: str,
    filename: str = "",
    ffprobe_binary: str = "ffprobe",
) -> dict[str, Any]:
    if not data:
        raise MediaValidationError("empty media")
    if content_type not in ALLOWED_TYPES:
        raise MediaValidationError("unsupported content type")
    if content_type in IMAGE_TYPES:
        from io import BytesIO

        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height, actual_format = image.width, image.height, image.format
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MediaValidationError("invalid image") from exc
        expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[content_type]
        if actual_format != expected:
            raise MediaValidationError("content type does not match image bytes")
        if width <= 0 or height <= 0 or width * height > 100_000_000:
            raise MediaValidationError("image dimensions are unsafe")
        return {
            "kind": "IMAGE",
            "width": width,
            "height": height,
            "duration_seconds": None,
            "fps": None,
            "frames": 1,
            "content_type": content_type,
        }
    suffix = Path(filename).suffix or mimetypes.guess_extension(content_type) or ".bin"
    with TemporaryDirectory(prefix="studio-inspect-") as directory:
        path = Path(directory) / f"input{suffix}"
        await asyncio.to_thread(path.write_bytes, data)
        metadata = await inspect_path(path, ffprobe_binary)
    expected_kind = kind_for_content_type(content_type)
    if metadata["kind"] != expected_kind:
        raise MediaValidationError("content type does not match media streams")
    formats = set(str(metadata.get("container_format") or "").split(","))
    allowed_formats = {
        "video/mp4": {"mov", "mp4"},
        "video/quicktime": {"mov", "mp4"},
        "video/webm": {"matroska", "webm"},
        "audio/wav": {"wav"},
        "audio/mpeg": {"mp3", "mpeg"},
        "audio/mp4": {"mov", "mp4", "m4a"},
        "audio/ogg": {"ogg"},
        "audio/flac": {"flac"},
    }
    if not formats & allowed_formats[content_type]:
        raise MediaValidationError("content type does not match media container")
    metadata["content_type"] = content_type
    return metadata


async def inspect_media_path(
    path: Path, content_type: str, ffprobe_binary: str = "ffprobe"
) -> dict[str, Any]:
    """Inspect an already bounded local file without reading it into RAM."""
    kind_for_content_type(content_type)
    if (await asyncio.to_thread(path.stat)).st_size <= 0:
        raise MediaValidationError("empty media")
    if content_type in IMAGE_TYPES:

        def inspect_image():
            try:
                with Image.open(path) as image:
                    width, height, actual_format = image.width, image.height, image.format
                    if width <= 0 or height <= 0 or width * height > 100_000_000:
                        raise MediaValidationError("image dimensions are unsafe")
                    image.verify()
            except (
                UnidentifiedImageError,
                OSError,
                ValueError,
                Image.DecompressionBombError,
            ) as exc:
                raise MediaValidationError("invalid image") from exc
            expected = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}[
                content_type
            ]
            if actual_format != expected:
                raise MediaValidationError("content type does not match image bytes")
            return {
                "kind": "IMAGE",
                "width": width,
                "height": height,
                "duration_seconds": None,
                "fps": None,
                "frames": 1,
                "content_type": content_type,
            }

        # Import lazily: worker primitives also import media validation.
        from workers.common import _await_owned_task

        inspection = asyncio.create_task(asyncio.to_thread(inspect_image))
        # Retain the file through repeated cancellation until Pillow closes it.
        return await _await_owned_task(inspection)
    metadata = await inspect_path(path, ffprobe_binary)
    if metadata["kind"] != kind_for_content_type(content_type):
        raise MediaValidationError("content type does not match media streams")
    formats = set(str(metadata.get("container_format") or "").split(","))
    allowed = {
        "video/mp4": {"mov", "mp4"},
        "video/quicktime": {"mov", "mp4"},
        "video/webm": {"matroska", "webm"},
        "audio/wav": {"wav"},
        "audio/mpeg": {"mp3", "mpeg"},
        "audio/mp4": {"mov", "mp4", "m4a"},
        "audio/ogg": {"ogg"},
        "audio/flac": {"flac"},
    }
    if not formats & allowed[content_type]:
        raise MediaValidationError("content type does not match media container")
    metadata["content_type"] = content_type
    return metadata
