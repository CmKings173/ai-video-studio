"""Bounded media inspection for uploaded and generated assets."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image, UnidentifiedImageError


class MediaValidationError(ValueError):
    pass


IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/webm"}
AUDIO_TYPES = {"audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg"}
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
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise MediaValidationError("media inspection timed out") from exc
    if process.returncode != 0:
        raise MediaValidationError(
            f"invalid media: {stderr.decode('utf-8', errors='replace')[-500:]}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MediaValidationError("ffprobe returned invalid metadata") from exc


async def inspect_path(path: Path, ffprobe_binary: str = "ffprobe") -> dict[str, Any]:
    value = await _ffprobe(path, ffprobe_binary)
    streams = value.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    duration_value = (value.get("format") or {}).get("duration")
    duration = float(duration_value) if duration_value not in (None, "N/A") else None
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
        "fps": fps,
        "frames": frames,
        "has_audio": bool(audio),
        "has_video": bool(video),
        "codec": video.get("codec_name") if video else audio.get("codec_name"),
    }


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
    metadata["content_type"] = content_type
    return metadata
