from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from apps.api.app.services.generation_intent import (
    GenerationAssetBinding as AssetBinding,
)
from apps.api.app.services.generation_intent import (
    GenerationCanvas as Canvas,
)
from apps.api.app.services.generation_intent import (
    GenerationTimelineSegment as TimelineSegment,
)
from apps.api.app.services.generation_intent import (
    PromptProvenance,
    stable_hash,
)

DirectorTask = Literal["t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"]
_SIGNING_TOKEN = object()


def artifact_member_range(binding: dict[str, Any], member_count: int | None) -> range:
    """Resolve qualified coverage against frozen runtime input, never observed output."""
    if "coverage" in binding:
        if (
            binding["coverage"] != "all_members"
            or binding.get("transport") != "studio_native_segments_v1"
            or binding.get("role") not in {"segment", "pre-refine", "pre-face"}
            or binding.get("required") is not True
            or "member_index" in binding
            or "count" in binding
            or type(member_count) is not int
            or member_count < 1
        ):
            raise ValueError("DIRECTOR_ARTIFACT_MEMBER_COVERAGE_INVALID")
        return range(member_count)
    start, count = binding.get("member_index", 0), binding.get("count", 1)
    if (
        type(start) is not int
        or start < 0
        or type(count) is not int
        or count < 1
        or (member_count is not None and start + count > member_count)
    ):
        raise ValueError("DIRECTOR_ARTIFACT_MEMBER_INVALID")
    return range(start, start + count)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DirectorExecutionSpec(FrozenModel):
    schema_version: Literal[1] = 1
    provider: Literal["minimax_h3_director"] = "minimax_h3_director"
    provider_version: str
    source_repository: str
    source_commit: str
    task: DirectorTask
    requested_mode: str
    prompt: str
    negative_prompt: str = ""
    prompt_provenance: PromptProvenance
    canvas: Canvas
    quality_profile: str
    seed: int = Field(ge=0, le=9_007_199_254_740_991)
    seed_policy: Literal["RANDOM", "FIXED"]
    steps: int = Field(ge=1, le=200)
    cfg: float = Field(ge=0.0, le=30.0)
    fps: Literal[24]
    frames: int = Field(ge=5, le=100000)
    requested_duration_seconds: float = Field(gt=0)
    resolved_duration_seconds: float = Field(gt=0)
    assets: tuple[AssetBinding, ...] = ()
    timeline: tuple[TimelineSegment, ...] = ()
    motion_context: dict[str, Any] = Field(default_factory=dict)
    refine: dict[str, Any] = Field(default_factory=dict)
    face_refine: dict[str, Any] = Field(default_factory=dict)
    audio_policy: dict[str, Any] = Field(default_factory=dict)
    output_prefix: str
    workflow_id: str
    workflow_code: str
    workflow_version: str
    workflow_hash: str
    slot_map_hash: str
    profile_hash: str
    custom_node_versions: dict[str, str] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    intent_hash: str
    execution_hash: str
    member_count: int | None = Field(default=None, strict=True, ge=1)

    @classmethod
    def finalize(cls, **values: Any) -> DirectorExecutionSpec:
        unsigned = dict(values)
        unsigned.pop("execution_hash", None)
        # Normalize nested models and defaults before hashing; the persisted JSON
        # must have the same identity as the in-memory provider contract.
        return cls.model_validate(
            {**unsigned, "execution_hash": ""}, context={"signing_token": _SIGNING_TOKEN}
        )

    @model_validator(mode="after")
    def verify_execution_hash(self, info: ValidationInfo) -> DirectorExecutionSpec:
        unsigned = self.model_dump(mode="json", exclude={"execution_hash"})
        # Pre-dynamic frozen executions omitted this field; preserve their signatures.
        if self.member_count is None:
            unsigned.pop("member_count", None)
        elif len(self.timeline) != self.member_count:
            raise ValueError("Director member count must match its native segment timeline")
        if info.context and info.context.get("signing_token") is _SIGNING_TOKEN:
            object.__setattr__(self, "execution_hash", stable_hash(unsigned))
            return self
        if self.execution_hash != stable_hash(unsigned):
            raise ValueError("Director execution hash does not match frozen semantics")
        return self
