"""Pure H3 request validation; runtime-specific values come from a profile."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


class H3ValidationError(ValueError):
    """Raised when a generation request violates an approved H3 profile."""


@dataclass(frozen=True)
class H3Profile:
    canvas_multiple: int = 32
    max_pixels: int = 8192 * 8192
    max_width: int = 8192
    max_height: int = 8192
    fps: int = 24
    frame_base: int = 5
    frame_step: int = 17
    max_reference_images: int = 9
    max_reference_audio: int = 3
    max_reference_videos: int = 3
    audio_min_seconds: float = 2.0
    audio_max_seconds: float = 15.0
    audio_total_max_seconds: float = 15.0
    video_min_seconds: float = 2.0
    video_max_seconds: float = 15.0
    video_total_max_seconds: float = 15.0
    max_total_reference_files: int = 12
    audio_requires_visual_reference: bool = True

    def __post_init__(self):
        for name in (
            "canvas_multiple",
            "max_pixels",
            "max_width",
            "max_height",
            "fps",
            "frame_base",
            "frame_step",
            "max_reference_images",
            "max_reference_audio",
            "max_reference_videos",
            "max_total_reference_files",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if (
            (self.canvas_multiple, self.fps, self.frame_base, self.frame_step)
            != (
                32,
                24,
                5,
                17,
            )
            or self.max_pixels == 0
            or self.max_width == 0
            or self.max_height == 0
        ):
            raise ValueError("Profile must preserve native H3 canvas/FPS/frame-grid parameters")
        for name in (
            "audio_min_seconds",
            "audio_max_seconds",
            "audio_total_max_seconds",
            "video_min_seconds",
            "video_max_seconds",
            "video_total_max_seconds",
        ):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite duration")
        if type(self.audio_requires_visual_reference) is not bool:
            raise ValueError("audio_requires_visual_reference must be boolean")


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
    source_video_asset_id: str | None = None
    reference_audio_durations: list[float] = field(default_factory=list)
    reference_video_durations: list[float] = field(default_factory=list)


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
        if request.mode not in {
            "t2v",
            "i2v",
            "fl2v",
            "i2v_last",
            "i2v_first_last",
            "r2v",
            "v2v",
            "rv2v",
        }:
            raise H3ValidationError("unsupported generation mode")
        if request.width <= 0 or request.height <= 0:
            raise H3ValidationError("width and height must be positive")
        if request.width % p.canvas_multiple or request.height % p.canvas_multiple:
            raise H3ValidationError(f"width and height must be a multiple of {p.canvas_multiple}")
        if request.width > p.max_width or request.height > p.max_height:
            raise H3ValidationError("canvas exceeds the H3 source bounds")
        if request.width * request.height > p.max_pixels:
            raise H3ValidationError("pixel area exceeds the H3 profile limit")
        if (
            type(request.duration_seconds) not in {int, float}
            or not math.isfinite(request.duration_seconds)
            or not 4 <= request.duration_seconds <= 15
        ):
            raise H3ValidationError("duration_seconds must be finite and between 4 and 15")

        has_references = any(
            (
                request.reference_image_asset_ids,
                request.reference_audio_asset_ids,
                request.reference_video_asset_ids,
            )
        )
        if request.mode == "t2v":
            if (
                request.first_frame_asset_id
                or request.last_frame_asset_id
                or request.source_video_asset_id
                or has_references
            ):
                raise H3ValidationError("t2v accepts no frame, source-video, or reference assets")
        elif request.mode in {"i2v_first_last", "fl2v"}:
            if not request.first_frame_asset_id or not request.last_frame_asset_id:
                if request.mode == "i2v_first_last":
                    raise H3ValidationError("i2v_first_last requires first_frame and last_frame")
                if not (request.first_frame_asset_id or request.last_frame_asset_id):
                    raise H3ValidationError("fl2v requires at least one endpoint frame")
            if has_references or request.source_video_asset_id:
                raise H3ValidationError(
                    "fl2v accepts endpoint frames, not references or source video"
                )
        elif request.mode == "i2v":
            if not request.first_frame_asset_id or request.last_frame_asset_id:
                raise H3ValidationError("i2v requires first_frame only")
            if has_references or request.source_video_asset_id:
                raise H3ValidationError("i2v accepts no reference assets")
        elif request.mode == "i2v_last":
            if (
                not request.last_frame_asset_id
                or request.first_frame_asset_id
                or request.source_video_asset_id
                or has_references
            ):
                raise H3ValidationError("i2v_last requires last_frame only")
        elif request.mode == "r2v":
            if (
                request.first_frame_asset_id
                or request.last_frame_asset_id
                or request.source_video_asset_id
            ):
                raise H3ValidationError("r2v accepts references, not frame assets")
            if not has_references:
                raise H3ValidationError("r2v requires at least one reference asset")
            if not (request.reference_image_asset_ids or request.reference_video_asset_ids):
                raise H3ValidationError("audio-only references are unsupported")
        elif request.mode == "v2v":
            if not request.source_video_asset_id:
                raise H3ValidationError("v2v requires source_video_asset_id")
            if request.first_frame_asset_id or request.last_frame_asset_id or has_references:
                raise H3ValidationError("v2v accepts a source video without extra references")
        elif request.mode == "rv2v":
            if not request.source_video_asset_id:
                raise H3ValidationError("rv2v requires source_video_asset_id")
            if request.first_frame_asset_id or request.last_frame_asset_id:
                raise H3ValidationError("rv2v does not accept endpoint frames")
            if not has_references:
                raise H3ValidationError("rv2v requires at least one reference asset")

        all_ids = [
            *request.reference_image_asset_ids,
            *request.reference_audio_asset_ids,
            *request.reference_video_asset_ids,
        ]
        if len(all_ids) != len(set(all_ids)):
            raise H3ValidationError("reference assets must be unique")
        if len(all_ids) > min(p.max_total_reference_files, 12):
            raise H3ValidationError("maximum twelve mixed reference files")

        self._check_count(
            "reference images", request.reference_image_asset_ids, min(p.max_reference_images, 9)
        )
        self._check_count(
            "reference audio", request.reference_audio_asset_ids, min(p.max_reference_audio, 3)
        )
        self._check_count(
            "reference videos", request.reference_video_asset_ids, min(p.max_reference_videos, 3)
        )
        if len(request.reference_audio_durations) != len(request.reference_audio_asset_ids):
            raise H3ValidationError("reference audio durations must match reference audio count")
        for duration in request.reference_audio_durations:
            if (
                type(duration) not in {int, float}
                or not math.isfinite(duration)
                or not max(2, p.audio_min_seconds) <= duration <= min(15, p.audio_max_seconds)
            ):
                raise H3ValidationError("reference audio duration is outside the profile limits")
        if sum(request.reference_audio_durations) > min(15, p.audio_total_max_seconds):
            raise H3ValidationError("total reference audio duration exceeds the profile limit")
        if len(request.reference_video_durations) != len(request.reference_video_asset_ids):
            raise H3ValidationError("reference video durations must match video count")
        for duration in request.reference_video_durations:
            if (
                type(duration) not in {int, float}
                or not math.isfinite(duration)
                or not max(2, p.video_min_seconds) <= duration <= min(15, p.video_max_seconds)
            ):
                raise H3ValidationError("reference video duration is outside profile limits")
        if sum(request.reference_video_durations) > min(15, p.video_total_max_seconds):
            raise H3ValidationError("total reference video duration exceeds profile limit")

        target_frames = max(5, round(request.duration_seconds * p.fps))
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
