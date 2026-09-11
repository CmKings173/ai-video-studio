from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")
Password = Annotated[
    str,
    StringConstraints(strip_whitespace=False, min_length=1, max_length=1024),
]
StrongPassword = Annotated[
    str,
    StringConstraints(strip_whitespace=False, min_length=12, max_length=1024),
]


class Page(Output, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: Password


class UserCreate(Input):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=255)
    password: StrongPassword
    role: Literal["ADMIN", "EDITOR"] = "EDITOR"


class UserPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None
    role: Literal["ADMIN", "EDITOR"] | None = None
    password: StrongPassword | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        invalid = [field for field in self.model_fields_set if getattr(self, field) is None]
        if invalid:
            raise ValueError(f"Patch fields cannot be null: {', '.join(sorted(invalid))}")
        return self


class UserDTO(Output):
    id: str
    email: str
    name: str
    role: Literal["ADMIN", "EDITOR"]
    is_active: bool
    created_at: datetime


class LoginDTO(Output):
    user: UserDTO
    csrf_token: str


class ResourceCreate(Input):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)


class ResourcePatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    archived: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        invalid = [
            field
            for field in self.model_fields_set
            if getattr(self, field) is None and field != "brand_id"
        ]
        if invalid:
            raise ValueError(f"Patch fields cannot be null: {', '.join(sorted(invalid))}")
        return self


class ProjectDTO(Output):
    id: str
    name: str
    description: str
    archived: bool
    revision: int
    created_at: datetime


class BrandCreate(ResourceCreate):
    context: dict[str, Any] = Field(default_factory=dict)


class BrandPatch(ResourcePatch):
    context: dict[str, Any] | None = None


class BrandDTO(ProjectDTO):
    context: dict[str, Any]


class ProductCreate(BrandCreate):
    brand_id: UUID | None = None


class ProductPatch(BrandPatch):
    brand_id: UUID | None = None


class ProductDTO(BrandDTO):
    brand_id: str | None


class VideoCreate(Input):
    project_id: UUID
    product_id: UUID | None = None
    brand_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    kind: Literal["QUICK_CLIP", "LONG_VIDEO"] = "QUICK_CLIP"
    target_duration: float = Field(default=5, ge=4, le=60)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    brief: str = Field(min_length=1, max_length=20000)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def duration_matches_kind(self):
        if self.kind == "QUICK_CLIP" and self.target_duration > 15:
            raise ValueError("Quick clips must be 4-15 seconds")
        if self.kind == "LONG_VIDEO" and self.target_duration not in (30, 60):
            raise ValueError("Long videos must be 30 or 60 seconds")
        return self


class VideoPatch(Input):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    brief: str | None = Field(default=None, min_length=1, max_length=20000)
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        invalid = [field for field in self.model_fields_set if getattr(self, field) is None]
        if invalid:
            raise ValueError(f"Patch fields cannot be null: {', '.join(sorted(invalid))}")
        return self


class VideoDTO(Output):
    id: str
    project_id: str
    product_id: str | None
    brand_id: str | None
    title: str
    kind: Literal["QUICK_CLIP", "LONG_VIDEO"]
    target_duration: float
    aspect_ratio: str
    brief: str
    config: dict[str, Any]
    status: str
    revision: int
    current_final_video_id: str | None
    created_at: datetime


class SceneSpec(Input):
    title: str = Field(default="", max_length=255)
    purpose: str = Field(default="PRODUCT_DETAIL", max_length=80)
    description: str = Field(default="", max_length=10000)
    subject: str = Field(default="", max_length=2000)
    action: str = Field(default="", max_length=2000)
    environment: str = Field(default="", max_length=2000)
    camera: str = Field(default="", max_length=2000)
    lighting: str = Field(default="", max_length=2000)
    style: str = Field(default="", max_length=2000)
    continuity: Literal["CUT", "CONTINUOUS"] = "CUT"
    dialogue: str = Field(default="", max_length=4000)
    soundscape: str = Field(default="", max_length=2000)


class SceneCreate(Input):
    scene_order: int | None = Field(default=None, ge=0)
    prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=10000)
    duration_seconds: float = Field(default=5, ge=4, le=15)
    spec: SceneSpec = Field(default_factory=SceneSpec)


class ScenePatch(Input):
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    negative_prompt: str | None = Field(default=None, max_length=10000)
    duration_seconds: float | None = Field(default=None, ge=4, le=15)
    spec: SceneSpec | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        invalid = [field for field in self.model_fields_set if getattr(self, field) is None]
        if invalid:
            raise ValueError(f"Patch fields cannot be null: {', '.join(sorted(invalid))}")
        return self


class SceneDTO(Output):
    id: str
    video_id: str
    scene_order: int
    prompt: str
    negative_prompt: str
    duration_seconds: float
    spec: dict[str, Any]
    enabled: bool
    revision: int
    selected_generation_id: str | None
    created_at: datetime


class VideoDetail(VideoDTO):
    scenes: list[SceneDTO]


class Reorder(Input):
    scene_ids: list[UUID] = Field(min_length=1, max_length=100)


class Selection(Input):
    generation_id: UUID


class StoryboardPublish(Input):
    scenes: list[SceneCreate] = Field(min_length=2, max_length=15)
    replace: bool = False
    replace_scene_revisions: dict[UUID, int] | None = None


class UploadRequest(Input):
    project_id: UUID | None = None
    product_id: UUID | None = None
    filename: str = Field(min_length=1, max_length=255)
    content_type: Literal[
        "image/png",
        "image/jpeg",
        "image/webp",
        "video/mp4",
        "video/webm",
        "audio/wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
    ]
    size_bytes: int = Field(gt=0)
    role: Literal[
        "PRODUCT_IMAGE",
        "PROJECT_REFERENCE",
        "REFERENCE_VIDEO",
        "REFERENCE_AUDIO",
        "BACKGROUND_AUDIO",
    ] = "PROJECT_REFERENCE"

    @model_validator(mode="after")
    def source_scope(self):
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("Provide exactly one project_id or product_id")
        if any(c in self.filename for c in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError("filename must be a basename")
        return self


class UploadDTO(Output):
    asset_id: str
    object_key: str
    upload: dict[str, Any]
    expires_in: int = 900


class AssetDTO(Output):
    id: str
    project_id: str | None
    product_id: str | None
    kind: str
    role: str
    filename: str
    content_type: str
    status: str
    size_bytes: int
    checksum: str | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    created_at: datetime


class AssetComplete(Input):
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class DownloadDTO(Output):
    url: str
    expires_in: int = 900


class GenerationRequest(Input):
    workflow_id: UUID | None = None
    mode: Literal["t2v", "i2v", "i2v_first_last", "r2v"] | None = None
    operation: Literal["ORIGINAL", "REGENERATE", "VARIATION"] = "ORIGINAL"
    parent_generation_id: UUID | None = None
    first_frame_asset_id: UUID | None = None
    last_frame_asset_id: UUID | None = None
    reference_image_asset_ids: list[UUID] = Field(default_factory=list, max_length=9)
    reference_audio_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reference_video_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    execution_prompt: str | None = Field(default=None, min_length=1, max_length=30000)
    source_scene_revision: int | None = Field(default=None, ge=1)
    source_video_revision: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int | None = Field(default=None, ge=32, le=4096)
    height: int | None = Field(default=None, ge=32, le=4096)
    steps: int = Field(default=8, ge=1, le=100)

    @model_validator(mode="after")
    def operation_parent(self):
        if self.operation == "VARIATION" and self.parent_generation_id is None:
            raise ValueError("VARIATION requires parent_generation_id")
        if self.operation != "VARIATION" and self.parent_generation_id is not None:
            raise ValueError("parent_generation_id is only valid for VARIATION")
        if bool(self.source_scene_revision) != bool(self.source_video_revision):
            raise ValueError("source scene/video revisions must be supplied together")
        return self


class GenerateAll(Input):
    settings: GenerationRequest = Field(default_factory=GenerationRequest)
    scene_ids: list[UUID] | None = Field(default=None, max_length=15)


class VariationRequest(Input):
    parent_generation_id: UUID
    workflow_id: UUID | None = None
    mode: Literal["t2v", "i2v", "i2v_first_last", "r2v"] | None = None
    first_frame_asset_id: UUID | None = None
    last_frame_asset_id: UUID | None = None
    reference_image_asset_ids: list[UUID] = Field(default_factory=list, max_length=9)
    reference_audio_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reference_video_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    execution_prompt: str | None = Field(default=None, min_length=1, max_length=30000)
    source_scene_revision: int | None = Field(default=None, ge=1)
    source_video_revision: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)
    width: int | None = Field(default=None, ge=32, le=4096)
    height: int | None = Field(default=None, ge=32, le=4096)
    steps: int = Field(default=8, ge=1, le=100)


class PromptPreviewDTO(Output):
    raw_prompt: str
    execution_prompt: str
    enhanced: bool
    warnings: list[str]
    scene_revision: int
    video_revision: int


class BatchGenerationDTO(Output):
    video_id: str
    generations: list["GenerationDTO"]


class GenerationDTO(Output):
    id: str
    video_id: str
    scene_id: str
    mode: str
    workflow_id: str
    generation_no: int
    operation: str
    parent_generation_id: str | None
    status: str
    phase: str | None
    progress_current: int | None
    progress_total: int | None
    revision: int
    input_snapshot: dict[str, Any]
    request_id: str | None
    comfy_prompt_id: str | None
    output_asset_id: str | None
    attempt_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    progress_updated_at: datetime | None
    finished_at: datetime | None


class GenerationAttemptDTO(Output):
    id: str
    generation_id: str
    attempt_no: int
    status: str
    client_id: str
    comfy_prompt_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None


class AssemblyRequest(Input):
    transition: Literal["CUT", "CROSSFADE"] = "CUT"
    crossfade_seconds: float = Field(default=0.5, ge=0.1, le=1.5)
    audio_mode: Literal["KEEP_SCENE_AUDIO", "MUTE_SCENE_AUDIO"] = "KEEP_SCENE_AUDIO"
    background_audio_asset_id: UUID | None = None
    background_volume: float = Field(default=0.3, ge=0, le=1)
    width: int = Field(default=1080, ge=256, le=1920, multiple_of=2)
    height: int = Field(default=1920, ge=256, le=1920, multiple_of=2)
    fps: Literal[24, 25, 30] = 24


class FinalDTO(Output):
    id: str
    video_id: str
    version_no: int
    status: str
    manifest: dict[str, Any]
    manifest_hash: str
    assembly_config: dict[str, Any]
    output_asset_id: str | None
    revision: int
    phase: str
    progress_current: int
    progress_total: int
    attempt_count: int
    request_id: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    progress_updated_at: datetime | None
    finished_at: datetime | None


class WorkflowCreate(Input):
    code: str = Field(min_length=1, max_length=100)
    mode: Literal["t2v", "i2v", "i2v_first_last", "r2v"]
    version: str = Field(min_length=1, max_length=50)
    workflow: dict[str, Any]
    slots: dict[str, tuple[str, str]]
    required_slots: list[str] = Field(default_factory=list)
    profile: dict[str, Any] = Field(default_factory=dict)


class WorkflowDTO(WorkflowCreate, Output):
    id: str
    workflow_hash: str
    slot_map_hash: str
    enabled: bool
    created_at: datetime
    approved_at: datetime | None


class WorkflowApproval(Input):
    enabled: bool


class ErrorDTO(Output):
    code: str
    message: str
    trace_id: str
    details: dict[str, Any] | list[Any] = Field(default_factory=dict)


class ErrorEnvelope(Output):
    error: ErrorDTO


class DashboardSummaryDTO(Output):
    projects: int
    videos: int
    assets_ready: int
    generations_pending: int
    generations_running: int
    generations_failed: int
    assemblies_pending: int


class RuntimeComponentDTO(Output):
    healthy: bool
    details: dict[str, Any] = Field(default_factory=dict)


class SystemStatusDTO(Output):
    status: Literal["ok", "degraded"]
    postgres: RuntimeComponentDTO
    minio: RuntimeComponentDTO
    comfyui: RuntimeComponentDTO
    local_storage: RuntimeComponentDTO


class StorageSummaryDTO(Output):
    database_assets: int
    database_bytes: int
    object_count: int
    object_bytes: int
    pending_assets: int
    failed_assets: int
    deleted_assets: int
    local_free_bytes: int


class CleanupRequest(Input):
    dry_run: bool = True
    pending_older_than_hours: int = Field(
        default=24,
        ge=1,
        le=24 * 365,
        deprecated=True,
        description="Deprecated compatibility field; runtime retention settings are authoritative.",
    )
    deleted_older_than_hours: int = Field(
        default=168,
        ge=1,
        le=24 * 365,
        deprecated=True,
        description="Deprecated compatibility field; runtime retention settings are authoritative.",
    )


class CleanupResultDTO(Output):
    dry_run: bool
    candidates: list[dict[str, Any]]
    deleted_objects: int
    retained_records: int


class ReconciliationDTO(Output):
    missing_objects: list[str]
    corrupt_objects: list[str]
    unavailable_objects: list[str]
    orphan_objects: list[str]
    repaired_assets: list[str]
