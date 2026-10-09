"""Release gates consume measured evidence, never static preflight success."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

from apps.api.app.core.errors import AppError

SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class MeasuredOutput(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    width: int = Field(gt=0, strict=True)
    height: int = Field(gt=0, strict=True)
    fps: float = Field(gt=0, strict=True)
    duration_seconds: float = Field(gt=0, strict=True)
    has_video: StrictBool
    has_audio: StrictBool
    checksum: SHA256
    size_bytes: int = Field(gt=0, strict=True)


class ExecutionEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    executed: StrictBool
    workflow_hash: SHA256
    slot_map_hash: SHA256
    tested_at: datetime
    prompt_id: str = Field(min_length=1)
    model_hash: SHA256
    lora_hashes: dict[str, SHA256]
    comfyui_commit: str = Field(pattern=r"^[a-f0-9]{40,64}$")
    custom_node_versions: dict[str, str]
    output: MeasuredOutput

    @field_validator("tested_at")
    @classmethod
    def timestamp_has_zone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("executed timestamp needs an explicit timezone")
        return value

    @field_validator("custom_node_versions")
    @classmethod
    def versions_are_named(cls, value: dict[str, str]) -> dict[str, str]:
        # An empty map is valid for a graph made entirely of native nodes.
        if any(not name.strip() or not version.strip() for name, version in value.items()):
            raise ValueError("custom node versions must identify every declared dependency")
        return value


@dataclass(frozen=True)
class QualificationStatus:
    qualified: bool
    lifecycle: str
    reason: str | None
    evidence: ExecutionEvidence | None = None


def qualification_status(
    profile: dict[str, Any], workflow_hash: str, slot_map_hash: str
) -> QualificationStatus:
    if not isinstance(profile, dict) or profile.get("poc_verified") is not True:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Target runtime executed PoC is required"
        )
    try:
        evidence = ExecutionEvidence.model_validate(profile.get("execution_evidence"))
    except ValidationError:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Complete executed runtime and media evidence is required"
        )
    if not evidence.executed:
        return QualificationStatus(False, "STATIC_VALIDATED", "PoC was not executed")
    if evidence.workflow_hash != workflow_hash or evidence.slot_map_hash != slot_map_hash:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed evidence belongs to different workflow or slots"
        )
    if not evidence.output.has_video or not evidence.output.has_audio:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed H3 output must contain measured video and audio"
        )
    if profile.get("model_hash", evidence.model_hash) != evidence.model_hash:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed model hash differs from profile"
        )
    if profile.get("lora_hashes", evidence.lora_hashes) != evidence.lora_hashes:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed LoRA hashes differ from profile"
        )
    declared_nodes = profile.get("custom_node_versions", {})
    if not isinstance(declared_nodes, dict) or any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(version, str)
        or not version.strip()
        for name, version in declared_nodes.items()
    ):
        return QualificationStatus(False, "STATIC_VALIDATED", "Invalid custom-node declaration")
    if profile.get("comfyui_commit", evidence.comfyui_commit) != evidence.comfyui_commit:
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed runtime differs from profile"
        )
    if any(
        evidence.custom_node_versions.get(name) != version
        for name, version in declared_nodes.items()
    ):
        return QualificationStatus(
            False, "STATIC_VALIDATED", "Executed custom-node versions differ from profile"
        )
    return QualificationStatus(True, "POC_EXECUTED", None, evidence)


def require_qualified(
    profile: dict[str, Any], workflow_hash: str, slot_map_hash: str
) -> ExecutionEvidence:
    status = qualification_status(profile, workflow_hash, slot_map_hash)
    if not status.qualified or status.evidence is None:
        raise AppError("WORKFLOW_NOT_QUALIFIED", status.reason or "Executed PoC is required", 409)
    return status.evidence
