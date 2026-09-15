"""Pure H3 request validation; runtime-specific values come from a profile."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


class H3ValidationError(ValueError):
    """Raised when a generation request violates an approved H3 profile."""


@dataclass(frozen=True)
class H3Profile:
    canvas_multiple: int = 32
    max_pixels: int = 1920 * 1088
    fps: int = 24
    frame_base: int = 5
    frame_step: int = 17
    max_reference_images: int = 9
    max_reference_audio: int = 3
    max_reference_videos: int = 3
    audio_min_seconds: float = 2.0
    audio_max_seconds: float = 15.0
    audio_total_max_seconds: float = 15.0


@dataclass(frozen=True)
class H3Request:
    mode: str
    width: int
    height: int
    duration_seconds: float
    first_frame_asset_id: str | None = None
    last_frame_asset_id: str | None = None
    reference_image_asset_ids: list[str] = field(default_factory=list)
    reference_audio_asset_ids: list[str] = field(default_factory=list)
    reference_video_asset_ids: list[str] = field(default_factory=list)
    reference_audio_durations: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class H3ValidatedRequest:
    mode: str
    width: int
    height: int
    duration_seconds: float
    frames: int


class H3Validator:
    def __init__(self, profile: H3Profile) -> None:
        self.profile = profile

    def validate(self, request: H3Request) -> H3ValidatedRequest:
        p = self.profile
        if request.mode not in {"t2v", "i2v", "i2v_first_last", "r2v"}:
            raise H3ValidationError("unsupported generation mode")
        if request.width <= 0 or request.height <= 0:
            raise H3ValidationError("width and height must be positive")
        if request.width % p.canvas_multiple or request.height % p.canvas_multiple:
            raise H3ValidationError(f"width and height must be a multiple of {p.canvas_multiple}")
        if request.width * request.height > p.max_pixels:
            raise H3ValidationError("pixel area exceeds the H3 profile limit")
        if request.duration_seconds <= 0:
            raise H3ValidationError("duration_seconds must be positive")

        has_references = any(
            (
                request.reference_image_asset_ids,
                request.reference_audio_asset_ids,
                request.reference_video_asset_ids,
            )
        )
        if request.mode == "t2v":
            if request.first_frame_asset_id or request.last_frame_asset_id or has_references:
                raise H3ValidationError("t2v accepts no frame or reference assets")
        elif request.mode == "i2v_first_last":
            if not request.first_frame_asset_id or not request.last_frame_asset_id:
                raise H3ValidationError("i2v_first_last requires first_frame and last_frame")
            if has_references:
                raise H3ValidationError("i2v_first_last accepts no reference assets")
        elif request.mode == "i2v":
            if not request.first_frame_asset_id or request.last_frame_asset_id:
                raise H3ValidationError("i2v requires first_frame only")
            if has_references:
                raise H3ValidationError("i2v accepts no reference assets")
        elif request.mode == "r2v":
            if request.first_frame_asset_id or request.last_frame_asset_id:
                raise H3ValidationError("r2v accepts references, not frame assets")
            if not has_references:
                raise H3ValidationError("r2v requires at least one reference asset")

        all_ids = [
            *request.reference_image_asset_ids,
            *request.reference_audio_asset_ids,
            *request.reference_video_asset_ids,
        ]
        if len(all_ids) != len(set(all_ids)):
            raise H3ValidationError("reference assets must be unique")

        self._check_count(
            "reference images", request.reference_image_asset_ids, p.max_reference_images
        )
        self._check_count(
            "reference audio", request.reference_audio_asset_ids, p.max_reference_audio
        )
        self._check_count(
            "reference videos", request.reference_video_asset_ids, p.max_reference_videos
        )
        if len(request.reference_audio_durations) != len(request.reference_audio_asset_ids):
            raise H3ValidationError("reference audio durations must match reference audio count")
        for duration in request.reference_audio_durations:
            if not p.audio_min_seconds <= duration <= p.audio_max_seconds:
                raise H3ValidationError("reference audio duration is outside the profile limits")
        if sum(request.reference_audio_durations) > p.audio_total_max_seconds:
            raise H3ValidationError("total reference audio duration exceeds the profile limit")

        target_frames = math.ceil(request.duration_seconds * p.fps)
        frames = (
            p.frame_base
            + max(0, math.ceil((target_frames - p.frame_base) / p.frame_step)) * p.frame_step
        )
        return H3ValidatedRequest(
            request.mode, request.width, request.height, request.duration_seconds, frames
        )

    @staticmethod
    def _check_count(label: str, values: list[str], maximum: int) -> None:
        if len(values) > maximum:
            raise H3ValidationError(f"{label} exceed the profile limit of {maximum}")
