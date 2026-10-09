from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")
JS_SAFE_MAX_SEED = 9_007_199_254_740_991
Password = Annotated[
    str,
    StringConstraints(strip_whitespace=False, min_length=1, max_length=1024),
]
StrongPassword = Annotated[
    str,
    StringConstraints(strip_whitespace=False, min_length=12, max_length=1024),
]

ASSET_ROLE_KINDS: dict[str, str] = {
    "PRODUCT_IMAGE": "IMAGE",
    "PROJECT_REFERENCE": "IMAGE",
    "SOURCE_VIDEO": "VIDEO",
    "REFERENCE_VIDEO": "VIDEO",
    "REFERENCE_AUDIO": "AUDIO",
    "BACKGROUND_AUDIO": "AUDIO",
}


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
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"] = (
        "9:16"
    )
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
    aspect_ratio: (
        Literal["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"] | None
    ) = None

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
    # Latest promoted final retained as history. It is semantically current only
    # while status == READY; dependency edits retain this ID and mark DIRTY.
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


GenerationMode = Literal[
    "t2v",
    "i2v",
    "fl2v",
    "i2v_last",
    "i2v_first_last",
    "r2v",
    "v2v",
    "rv2v",
]
DirectorTask = Literal["t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v"]
QualityProfile = Literal["DRAFT", "STANDARD", "HIGH", "BASE", "HD", "FULL_HD_REFINED", "CUSTOM"]
GenerationRatio = Literal["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"]
ExactPrompt = Annotated[
    str, StringConstraints(strip_whitespace=False, min_length=1, max_length=30000)
]


class DirectorMotionContext(Input):
    enabled: bool = False
    context_frames: Literal[5, 22, 39, 56] = 22
    audio_context_frames: Literal[24] = 24
    continuity: Literal[True] = True
    keep_tail: bool = False


class DirectorRefine(Input):
    enabled: bool = False
    mode: Literal["refine", "upscale", "latent_upscale"] = "refine"
    upscale_method: Literal["h3_latent", "lanczos", "nvidia_rtx_vsr"] = "h3_latent"
    passes: int = Field(default=1, ge=1, le=4)
    seed_mode: Literal["inherit", "offset", "independent"] = "inherit"
    aspect_ratio: Literal["follow_director"] = "follow_director"
    megapixels: float = Field(default=0.0, ge=0.0, le=16.0)
    width: int = Field(default=0, ge=0, le=8192, multiple_of=32)
    height: int = Field(default=0, ge=0, le=8192, multiple_of=32)
    skip_fl2v: bool = True
    enable_latent_chunking: bool = False
    enable_tiling: bool = False

    @model_validator(mode="after")
    def supported_refine_geometry(self):
        # Pinned Refine.pack always forces follow-Director aspect; custom width /
        # height are ignored by resolve_refine_target. Reject that false promise.
        if self.enabled and (self.width or self.height):
            raise ValueError("Pinned Director Refine supports megapixels, not custom width/height")
        return self


class DirectorFaceRefine(Input):
    enabled: bool = False
    detector: Literal["face_yolov8m.pt"] = "face_yolov8m.pt"
    confidence: float = Field(default=0.35, ge=0.05, le=0.95)
    crop_factor: float = Field(default=2.5, ge=1.2, le=8.0)
    canvas_width: int = Field(default=768, ge=128, le=1344, multiple_of=32)
    canvas_height: int = Field(default=768, ge=128, le=1344, multiple_of=32)
    canvas_mode: Literal["manual", "auto_capped_768"] = "manual"
    select: Literal["largest_face", "centre_most"] = "largest_face"
    denoise: float = Field(default=0.40, ge=0.02, le=1.0)
    steps: int = Field(default=8, ge=1, le=50)
    seed_mode: Literal["inherit", "offset"] = "inherit"
    paste_region: Literal["face_only", "full_crop"] = "face_only"
    mask_dilation: int = Field(default=16, ge=0, le=256)
    feather: int = Field(default=24, ge=0, le=256)
    colour_match: float = Field(default=1.0, ge=0.0, le=1.0)
    blend: float = Field(default=1.0, ge=0.0, le=1.0)


class DirectorAudioPolicy(Input):
    mode: Literal["generate", "source", "mute"] = "generate"
    preserve_source_audio: Literal[True] = True


class DirectorTimelineSegment(Input):
    scene_id: UUID | None = None
    prompt: str = Field(default="", max_length=30000)
    start_frame: int = Field(default=0, ge=0)
    frame_count: int = Field(default=124, ge=5, le=100000)
    continuity_from_previous: bool = False


class SceneGenerationConfig(Input):
    mode: GenerationMode | Literal["AUTO"] = "AUTO"
    quality_profile: QualityProfile = "STANDARD"
    aspect_ratio: GenerationRatio | None = None
    first_frame_asset_id: UUID | None = None
    last_frame_asset_id: UUID | None = None
    reference_image_asset_ids: list[UUID] = Field(default_factory=list, max_length=9)
    reference_video_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reference_audio_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    source_video_asset_id: UUID | None = None
    seed_policy: Literal["RANDOM", "FIXED"] = "RANDOM"
    seed: int | None = Field(default=None, ge=0, le=JS_SAFE_MAX_SEED)
    cfg: float = Field(default=1.0, ge=0.0, le=30.0)
    width: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    height: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    fps: Literal[24] | None = None
    frames: int | None = Field(default=None, ge=5, le=100000)
    motion_context: DirectorMotionContext = Field(default_factory=DirectorMotionContext)
    refine: DirectorRefine = Field(default_factory=DirectorRefine)
    face_refine: DirectorFaceRefine = Field(default_factory=DirectorFaceRefine)
    audio_policy: DirectorAudioPolicy = Field(default_factory=DirectorAudioPolicy)

    @model_validator(mode="after")
    def fixed_seed(self):
        if self.seed_policy == "FIXED" and self.seed is None:
            raise ValueError("FIXED seed_policy requires seed")
        return self


class SceneCreate(Input):
    scene_order: int | None = Field(default=None, ge=0)
    prompt: str = Field(min_length=1, max_length=20000)
    negative_prompt: str = Field(default="", max_length=10000)
    duration_seconds: float = Field(default=5, ge=4, le=15)
    spec: SceneSpec = Field(default_factory=SceneSpec)
    generation_config: SceneGenerationConfig = Field(default_factory=SceneGenerationConfig)


class ScenePatch(Input):
    generation_config: SceneGenerationConfig | None = None
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
    selected_generation_fresh: bool = False
    generation_config: dict[str, Any] = Field(default_factory=dict)
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
        "video/quicktime",
        "audio/wav",
        "audio/mpeg",
        "audio/mp4",
        "audio/ogg",
        "audio/flac",
    ]
    size_bytes: int = Field(gt=0)
    role: Literal[
        "PRODUCT_IMAGE",
        "PROJECT_REFERENCE",
        "REFERENCE_VIDEO",
        "REFERENCE_AUDIO",
        "SOURCE_VIDEO",
        "BACKGROUND_AUDIO",
    ] = "PROJECT_REFERENCE"

    @model_validator(mode="after")
    def source_scope(self):
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("Provide exactly one project_id or product_id")
        if any(c in self.filename for c in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError("filename must be a basename")
        expected_kind = ASSET_ROLE_KINDS.get(self.role)
        actual_kind = self.content_type.partition("/")[0].upper()
        if expected_kind is None or actual_kind != expected_kind:
            required = expected_kind.lower() if expected_kind else "supported"
            raise ValueError(f"{self.role} requires a {required} upload")
        return self


class UploadDTO(Output):
    asset_id: str
    object_key: str
    upload: dict[str, Any]
    expires_in: int = 900


class AssetDTO(Output):
    media_metadata: dict[str, Any] = Field(default_factory=dict)
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
    retry_validation: bool = False
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class DownloadDTO(Output):
    url: str
    expires_in: int = 900


class GenerationRequest(Input):
    workflow_id: UUID | None = None
    mode: GenerationMode | Literal["AUTO"] | None = None
    operation: Literal["ORIGINAL", "REGENERATE", "VARIATION"] = "ORIGINAL"
    parent_generation_id: UUID | None = None
    first_frame_asset_id: UUID | None = None
    last_frame_asset_id: UUID | None = None
    reference_image_asset_ids: list[UUID] = Field(default_factory=list, max_length=9)
    reference_audio_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reference_video_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    source_video_asset_id: UUID | None = None
    execution_prompt: ExactPrompt | None = None
    source_scene_revision: int | None = Field(default=None, ge=1)
    source_video_revision: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=JS_SAFE_MAX_SEED)
    width: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    height: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    fps: Literal[24] | None = None
    frames: int | None = Field(default=None, ge=5, le=100000)
    quality_profile: QualityProfile | None = None
    aspect_ratio: GenerationRatio | None = None
    seed_policy: Literal["RANDOM", "FIXED"] | None = None
    motion_context: DirectorMotionContext | None = None
    refine: DirectorRefine | None = None
    face_refine: DirectorFaceRefine | None = None
    audio_policy: DirectorAudioPolicy | None = None
    timeline: list[DirectorTimelineSegment] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def operation_parent(self):
        if self.operation in {"VARIATION", "REGENERATE"} and self.parent_generation_id is None:
            raise ValueError("Parent operation requires parent_generation_id")
        if self.operation == "ORIGINAL" and self.parent_generation_id is not None:
            raise ValueError("parent_generation_id requires a parent operation")
        if bool(self.source_scene_revision) != bool(self.source_video_revision):
            raise ValueError("source scene/video revisions must be supplied together")
        if self.execution_prompt is not None and self.source_scene_revision is None:
            raise ValueError("Accepted execution_prompt requires source scene/video revisions")
        if self.seed_policy == "FIXED" and self.seed is None and self.operation == "ORIGINAL":
            raise ValueError("FIXED seed_policy requires seed")
        return self


class GenerateAll(Input):
    settings: GenerationRequest = Field(default_factory=GenerationRequest)
    scene_ids: list[UUID] | None = Field(default=None, max_length=15)
    expected_video_revision: int | None = Field(default=None, ge=1)
    expected_scene_revisions: dict[UUID, Annotated[int, Field(ge=1)]] | None = None

    @model_validator(mode="after")
    def paired_batch_revisions(self):
        if (self.expected_video_revision is None) != (self.expected_scene_revisions is None):
            raise ValueError("expected video and scene revisions must be supplied together")
        return self


class VariationRequest(Input):
    parent_generation_id: UUID
    workflow_id: UUID | None = None
    mode: GenerationMode | Literal["AUTO"] | None = None
    first_frame_asset_id: UUID | None = None
    last_frame_asset_id: UUID | None = None
    reference_image_asset_ids: list[UUID] = Field(default_factory=list, max_length=9)
    reference_audio_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    reference_video_asset_ids: list[UUID] = Field(default_factory=list, max_length=3)
    source_video_asset_id: UUID | None = None
    execution_prompt: ExactPrompt | None = None
    source_scene_revision: int | None = Field(default=None, ge=1)
    source_video_revision: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0, le=JS_SAFE_MAX_SEED)
    width: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    height: int | None = Field(default=None, ge=32, le=8192, multiple_of=32)
    steps: int | None = Field(default=None, ge=1, le=100)
    cfg: float | None = Field(default=None, ge=0.0, le=30.0)
    fps: Literal[24] | None = None
    frames: int | None = Field(default=None, ge=5, le=100000)
    quality_profile: QualityProfile | None = None
    aspect_ratio: GenerationRatio | None = None
    seed_policy: Literal["RANDOM", "FIXED"] | None = None
    motion_context: DirectorMotionContext | None = None
    refine: DirectorRefine | None = None
    face_refine: DirectorFaceRefine | None = None
    audio_policy: DirectorAudioPolicy | None = None
    timeline: list[DirectorTimelineSegment] | None = Field(default=None, max_length=100)


class RegenerateRequest(Input):
    parent_generation_id: UUID
    seed: int | None = Field(default=None, ge=0, le=JS_SAFE_MAX_SEED)
    seed_policy: Literal["RANDOM", "FIXED"] | None = None


class GenerationCapabilitiesDTO(Output):
    available: bool
    combinations: list[dict[str, Any]]
    disabled: list[dict[str, Any]]
    source_capabilities: dict[str, Any] = Field(default_factory=dict)
    qualified_capabilities: dict[str, Any] = Field(default_factory=dict)


class PromptPreviewRequest(GenerationRequest):
    pass


class PromptPreviewDTO(Output):
    raw_prompt: str
    execution_prompt: str
    enhanced: bool
    warnings: list[str]
    scene_revision: int
    video_revision: int


class ExecutionGroupDTO(Output):
    execution_scope: Literal["single_scene", "aggregate"]
    scene_ids: list[str]
    generation_ids: list[str]
    director_run_id: str | None = None


class BatchGenerationDTO(Output):
    video_id: str
    generations: list["GenerationDTO"]
    director_run_id: str | None = Field(
        default=None,
        json_schema_extra={"deprecated": True},
        description="Compatibility field for exactly one aggregate run; use execution_groups.",
    )
    execution_groups: list[ExecutionGroupDTO] = Field(default_factory=list)


class GenerationDTO(Output):
    output_metadata: dict[str, Any] = Field(default_factory=dict)
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
    delivery_preset: (
        Literal[
            "SOCIAL_VERTICAL_1080",
            "LANDSCAPE_FHD",
            "SQUARE_1080",
            "PORTRAIT_4_5",
            "PORTRAIT_3_4",
            "LANDSCAPE_4_3",
            "ULTRAWIDE_2560_1080",
        ]
        | None
    ) = None
    fit_mode: Literal["FIT_PAD", "CENTER_CROP"] = "FIT_PAD"
    width: int | None = Field(default=None, ge=256, le=4096, multiple_of=2)
    height: int | None = Field(default=None, ge=256, le=4096, multiple_of=2)
    fps: Literal[24, 25, 30] = 24

    @model_validator(mode="after")
    def valid_delivery(self):
        from apps.api.app.services.delivery_presets import resolve_delivery

        resolve_delivery(
            preset=self.delivery_preset,
            width=self.width,
            height=self.height,
            fps=self.fps,
            fit_mode=self.fit_mode,
        )
        return self


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
    mode: GenerationMode
    quality_profile: QualityProfile = "STANDARD"
    execution_scope: Literal["single_scene", "aggregate"] = "single_scene"
    version: str = Field(min_length=1, max_length=128)
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
    has_more: bool = False


class ReconciliationDTO(Output):
    missing_objects: list[str]
    corrupt_objects: list[str]
    unavailable_objects: list[str]
    orphan_objects: list[str]
    repaired_assets: list[str]
