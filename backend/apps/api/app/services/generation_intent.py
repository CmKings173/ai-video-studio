"""Provider-neutral immutable generation intent persisted before provider planning."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FrozenIntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class GenerationCanvas(FrozenIntentModel):
    width: int = Field(ge=32, le=8192, multiple_of=32)
    height: int = Field(ge=32, le=8192, multiple_of=32)
    aspect_ratio: str


class GenerationAssetBinding(FrozenIntentModel):
    id: str
    role: Literal[
        "FIRST_FRAME",
        "LAST_FRAME",
        "SOURCE_VIDEO",
        "REFERENCE_IMAGE",
        "REFERENCE_VIDEO",
        "REFERENCE_AUDIO",
    ]
    order_index: int = Field(ge=0)
    checksum: str
    filename: str
    content_type: str
    object_key: str = ""
    size_bytes: int = Field(default=0, ge=0)
    media_metadata: dict[str, Any] = Field(default_factory=dict)


class PromptProvenance(FrozenIntentModel):
    accepted: bool = False
    source_scene_revision: int | None = None
    source_video_revision: int | None = None
    inherited_from: str | None = None


class GenerationTimelineSegment(FrozenIntentModel):
    scene_id: str | None = None
    prompt: str = ""
    start_frame: int = Field(default=0, ge=0)
    frame_count: int = Field(default=124, ge=5, le=100000)
    continuity_from_previous: bool = False
    asset_indices: dict[str, list[int]] = Field(default_factory=dict)


class GenerationIntent(FrozenIntentModel):
    schema_version: Literal[1] = 1
    requested_mode: str
    provider_task: Literal["t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"]
    prompt: str
    negative_prompt: str = ""
    prompt_provenance: PromptProvenance = Field(default_factory=PromptProvenance)
    canvas: GenerationCanvas
    quality_profile: str
    seed: int = Field(ge=0, le=9_007_199_254_740_991)
    seed_policy: Literal["RANDOM", "FIXED"]
    steps: int = Field(ge=1, le=200)
    cfg: float = Field(default=1.0, ge=0.0, le=30.0)
    fps: int = Field(default=24, ge=1, le=240)
    frames: int = Field(ge=5, le=100000)
    requested_duration_seconds: float = Field(gt=0)
    resolved_duration_seconds: float = Field(gt=0)
    assets: tuple[GenerationAssetBinding, ...] = ()
    timeline: tuple[GenerationTimelineSegment, ...] = ()
    motion_context: dict[str, Any] = Field(default_factory=dict)
    refine: dict[str, Any] = Field(default_factory=dict)
    face_refine: dict[str, Any] = Field(default_factory=dict)
    audio_policy: dict[str, Any] = Field(default_factory=dict)

    @property
    def semantic_hash(self) -> str:
        return stable_hash(self.model_dump(mode="json"))
