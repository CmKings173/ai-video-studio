export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface UserDTO {
  id: string;
  email: string;
  name: string;
  role: "ADMIN" | "EDITOR";
  is_active: boolean;
  created_at: string;
}

export interface LoginDTO {
  user: UserDTO;
  csrf_token: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface UserCreateRequest {
  email: string;
  name: string;
  password: string;
  role?: "ADMIN" | "EDITOR";
}

export interface UserPatchRequest {
  name?: string;
  is_active?: boolean;
  role?: "ADMIN" | "EDITOR";
  password?: string;
}

export interface ProjectDTO {
  id: string;
  name: string;
  description: string;
  archived: boolean;
  revision: number;
  created_at: string;
}

export interface ResourceCreate {
  name: string;
  description?: string;
}

export interface ResourcePatch {
  name?: string;
  description?: string;
  archived?: boolean;
}

export interface BrandDTO extends ProjectDTO {
  context: Record<string, unknown>;
}

export interface BrandCreate extends ResourceCreate {
  context?: Record<string, unknown>;
}

export interface BrandPatch extends ResourcePatch {
  context?: Record<string, unknown>;
}

export interface ProductDTO extends BrandDTO {
  brand_id: string | null;
}

export interface ProductCreate extends BrandCreate {
  brand_id?: string | null;
}

export interface ProductPatch extends BrandPatch {
  brand_id?: string | null;
}

export type VideoKind = "QUICK_CLIP" | "LONG_VIDEO";
export type GenerationAspectRatio =
  | "16:9"
  | "9:16"
  | "1:1"
  | "4:3"
  | "3:4"
  | "3:2"
  | "2:3"
  | "21:9"
  | "Custom";
export type AspectRatio = GenerationAspectRatio;

export interface VideoDTO {
  id: string;
  project_id: string;
  product_id: string | null;
  brand_id: string | null;
  title: string;
  kind: VideoKind;
  target_duration: number;
  aspect_ratio: AspectRatio | string;
  brief: string;
  config: Record<string, unknown>;
  status: string;
  revision: number;
  /** Latest promoted artifact; semantically current only when status is READY. */
  current_final_video_id: string | null;
  created_at: string;
}

export interface VideoCreate {
  project_id: string;
  product_id?: string | null;
  brand_id?: string | null;
  title: string;
  kind?: VideoKind;
  target_duration?: number;
  aspect_ratio?: AspectRatio;
  brief: string;
  config?: Record<string, unknown>;
}

export interface VideoPatch {
  title?: string;
  brief?: string;
  config?: Record<string, unknown>;
}

export interface SceneSpec {
  title?: string;
  purpose?: string;
  description?: string;
  subject?: string;
  action?: string;
  environment?: string;
  camera?: string;
  lighting?: string;
  style?: string;
  continuity?: "CUT" | "CONTINUOUS";
  dialogue?: string;
  soundscape?: string;
}

export interface SceneDTO {
  id: string;
  video_id: string;
  scene_order: number;
  prompt: string;
  negative_prompt: string;
  duration_seconds: number;
  spec: SceneSpec;
  generation_config: SceneGenerationConfig;
  enabled: boolean;
  revision: number;
  selected_generation_id: string | null;
  selected_generation_fresh: boolean;
  created_at: string;
}

export interface SceneCreate {
  scene_order?: number;
  prompt: string;
  negative_prompt?: string;
  duration_seconds?: number;
  spec?: SceneSpec;
  generation_config?: SceneGenerationConfig;
}

export interface ScenePatch {
  prompt?: string;
  negative_prompt?: string;
  duration_seconds?: number;
  spec?: SceneSpec;
  enabled?: boolean;
  generation_config?: SceneGenerationConfig | null;
}

export interface VideoDetail extends VideoDTO {
  scenes: SceneDTO[];
}

export interface ReorderRequest {
  scene_ids: string[];
}

export interface SelectionRequest {
  generation_id: string;
}

export interface StoryboardPublishRequest {
  scenes: SceneCreate[];
  replace?: boolean;
  replace_scene_revisions?: Record<string, number>;
}

export interface StoryboardScenePreview {
  scene_order: number;
  title?: string;
  purpose?: string;
  duration_seconds: number;
  prompt: string;
  negative_prompt?: string;
  spec?: Record<string, unknown>;
}

export interface StoryboardPreviewDTO {
  scenes: StoryboardScenePreview[];
  total_seconds: number;
  source: string;
  warnings: string[];
  video_revision?: number;
}

export type AssetRole =
  | "PRODUCT_IMAGE"
  | "PROJECT_REFERENCE"
  | "SOURCE_VIDEO"
  | "REFERENCE_VIDEO"
  | "REFERENCE_AUDIO"
  | "BACKGROUND_AUDIO";

export interface UploadRequest {
  project_id?: string | null;
  product_id?: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  role?: AssetRole;
}

export interface UploadDTO {
  asset_id: string;
  object_key: string;
  upload: {
    url?: string;
    fields?: Record<string, string>;
    completed?: boolean;
  };
  expires_in: number;
}

export interface AssetDTO {
  id: string;
  project_id: string | null;
  product_id: string | null;
  kind: string;
  role: string;
  filename: string;
  content_type: string;
  status: string;
  size_bytes: number;
  checksum: string | null;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  media_metadata: Record<string, unknown>;
  created_at: string;
}

export interface AssetComplete {
  retry_validation?: boolean;
  checksum_sha256?: string | null;
}

export interface DownloadDTO {
  url: string;
  expires_in: number;
}

export type GenerationMode = "t2v" | "i2v" | "fl2v" | "i2v_last" | "i2v_first_last" | "r2v" | "v2v" | "rv2v";
export type QualityProfile = "DRAFT" | "STANDARD" | "HIGH" | "BASE" | "HD" | "FULL_HD_REFINED" | "CUSTOM";
export type SeedPolicy = "RANDOM" | "FIXED";

export interface MotionContextSettings {
  enabled: boolean;
  context_frames?: 5 | 22 | 39 | 56;
  [key: string]: unknown;
}

export interface RefineSettings {
  enabled: boolean;
  mode?: "refine" | "upscale" | "latent_upscale";
  upscale_method?: "h3_latent" | "lanczos" | "nvidia_rtx_vsr";
  passes?: number;
  seed_mode?: "inherit" | "offset" | "independent";
  aspect_ratio?: "follow_director";
  megapixels?: number;
  width?: number;
  height?: number;
  target_width?: number | null;
  target_height?: number | null;
  skip_fl2v?: boolean;
  enable_latent_chunking?: boolean;
  enable_tiling?: boolean;
  [key: string]: unknown;
}

export interface FaceRefineSettings {
  enabled: boolean;
  detector?: "face_yolov8m.pt";
  confidence?: number;
  crop_factor?: number;
  canvas_width?: number;
  canvas_height?: number;
  canvas_mode?: "manual" | "auto_capped_768";
  select?: "largest_face" | "centre_most";
  denoise?: number;
  steps?: number;
  seed_mode?: "inherit" | "offset";
  paste_region?: "face_only" | "full_crop";
  mask_dilation?: number;
  feather?: number;
  colour_match?: number;
  blend?: number;
  [key: string]: unknown;
}

export interface DirectorAudioSettings {
  enabled: boolean;
  preserve_native_audio?: boolean;
  [key: string]: unknown;
}

export interface DirectorAudioPolicy {
  mode: "generate" | "source" | "mute";
  preserve_source_audio?: boolean;
}

export interface FrozenDirectorExecutionSpec {
  schema_version: number;
  provider: "minimax_h3_director";
  provider_version: string;
  source_repository: string;
  source_commit: string;
  task: string;
  prompt: string;
  canvas: { width: number; height: number; aspect_ratio: GenerationAspectRatio };
  seed: number;
  steps: number;
  cfg: number;
  fps: number;
  frames: number;
  requested_duration_seconds: number;
  resolved_duration_seconds: number;
  motion_context: MotionContextSettings;
  refine: RefineSettings;
  face_refine: FaceRefineSettings;
  audio_policy: DirectorAudioPolicy;
  assets: GenerationInputAssetSnapshot[];
  execution_hash: string;
}

export interface DirectorExecutionSpec {
  schema_version?: number;
  provider?: {
    id?: string;
    repository?: string;
    commit?: string;
    comfyui_version?: string;
    release_qualification_id?: string;
    workflow_graph_hash?: string;
    slot_contract_hash?: string;
    [key: string]: unknown;
  };
  intent?: {
    business_mode?: GenerationMode | string;
    director_task?: string;
    accepted_prompt?: string;
    effective_prompt_metadata?: Record<string, unknown>;
    [key: string]: unknown;
  };
  generation?: {
    width?: number;
    height?: number;
    fps?: number;
    frame_count?: number;
    duration_seconds?: number;
    seed?: number;
    steps?: number;
    cfg?: number;
    [key: string]: unknown;
  };
  inputs?: Array<{
    role?: string;
    asset_id?: string;
    ordinal?: number;
    [key: string]: unknown;
  }>;
  continuity?: MotionContextSettings & { mode?: string; hard_cut_boundaries?: number[] };
  audio?: DirectorAudioSettings | Record<string, unknown>;
  refine?: RefineSettings | Record<string, unknown>;
  face_refine?: FaceRefineSettings | Record<string, unknown>;
  [key: string]: unknown;
}

export interface SceneGenerationConfig {
  mode?: GenerationMode | "AUTO";
  quality_profile?: QualityProfile;
  aspect_ratio?: GenerationAspectRatio | null;
  seed_policy?: SeedPolicy;
  seed?: number | null;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
  source_video_asset_id?: string | null;
  reference_image_asset_ids?: string[];
  reference_video_asset_ids?: string[];
  reference_audio_asset_ids?: string[];
  width?: number | null;
  height?: number | null;
  cfg?: number;
  fps?: 24;
  frames?: number;
  audio_policy?: DirectorAudioPolicy;
  motion_context?: MotionContextSettings;
  refine?: RefineSettings;
  face_refine?: FaceRefineSettings;
  audio?: DirectorAudioSettings;
}

export interface GenerationCapability {
  workflow_id: string;
  mode: GenerationMode;
  quality_profile: QualityProfile;
  aspect_ratio: GenerationAspectRatio;
  resolved_width: number;
  resolved_height: number;
  steps: number;
  fps: number;
  workflow_version: string;
  workflow_hash: string;
  required_asset_slots: string[];
  max_reference_images: number;
  max_reference_videos: number;
  max_reference_audio: number;
  max_total_reference_files: number;
  clip_min_seconds: number;
  clip_max_seconds: number;
  category_total_max_seconds: number;
  reference_video_fps: number;
  audio_requires_visual_reference: boolean;
  task?: string;
  provider?: string;
  supports_source_video?: boolean;
  execution_scope?: "single_scene" | "aggregate";
  supports_motion_context?: boolean;
  supports_refine?: boolean;
  supports_face_refine?: boolean;
  supports_audio?: boolean;
  audio_modes?: Array<"generate" | "source" | "mute">;
  director_settings?: Array<Pick<SceneGenerationConfig, "motion_context" | "refine" | "face_refine" | "audio_policy">>;
}

export interface GenerationSourceCapabilities {
  tasks?: Array<"t2v" | "i2v" | "fl2v" | "r2v" | "v2v" | "rv2v">;
  ratios?: GenerationAspectRatio[];
  custom_canvas?: { min: number; max: number; multiple: number };
  reference_limits?: { images?: number; videos?: number; audio?: number; total?: number };
  supports?: {
    source_video: boolean;
    first_frame: boolean;
    last_frame: boolean;
    motion_context: boolean;
    refine: boolean;
    face_refine: boolean;
    audio: boolean;
  };
  file_types?: { image?: string[]; video?: string[]; audio?: string[] };
  quality_profiles?: string[];
  [key: string]: unknown;
}

export interface GenerationCapabilitiesDTO {
  available: boolean;
  combinations?: GenerationCapability[];
  source_capabilities?: GenerationSourceCapabilities;
  qualified_capabilities?: GenerationCapability[] | { combinations?: GenerationCapability[]; [key: string]: unknown };
  disabled: { workflow_id: string; mode: string; quality_profile: string; reason: string; code: string }[];
}
export type GenerationOperation = "ORIGINAL" | "REGENERATE" | "VARIATION";
export type GenerationStatus =
  | "CREATED"
  | "DISPATCHING"
  | "QUEUED"
  | "RUNNING"
  | "COLLECTING"
  | "CANCEL_REQUESTED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type GenerationInputAssetRole =
  | "FIRST_FRAME"
  | "LAST_FRAME"
  | "SOURCE_VIDEO"
  | "REFERENCE_IMAGE"
  | "REFERENCE_AUDIO"
  | "REFERENCE_VIDEO";

export interface GenerationInputAssetSnapshot {
  id: string;
  object_key?: string;
  checksum?: string | null;
  role: GenerationInputAssetRole;
  order_index?: number;
  filename?: string;
  content_type?: string;
}

export interface GenerationInputSnapshot {
  schema_version?: number;
  mode?: GenerationMode;
  workflow_id?: string;
  scene_revision?: number;
  video_revision?: number;
  raw_prompt?: string;
  prompt?: string;
  prompt_enhanced?: boolean;
  prompt_warnings?: string[];
  negative_prompt?: string;
  seed?: number;
  width?: number;
  height?: number;
  frames?: number;
  duration_seconds?: number;
  steps?: number;
  requested_quality_profile?: string;
  requested_aspect_ratio?: string;
  requested_width?: number;
  requested_height?: number;
  requested_duration_seconds?: number;
  requested_fps?: number;
  requested_frames?: number;
  resolved_width?: number;
  resolved_height?: number;
  resolved_duration_seconds?: number;
  resolved_fps?: number;
  resolved_frames?: number;
  task?: string;
  provider?: string;
  provider_version?: string;
  workflow_version?: string;
  motion_context?: MotionContextSettings;
  refine?: RefineSettings;
  face_refine?: FaceRefineSettings;
  audio?: DirectorAudioSettings;
  director_execution_spec?: DirectorExecutionSpec;
  audio_policy?: DirectorAudioPolicy;
  director_execution?: FrozenDirectorExecutionSpec;
  assets?: GenerationInputAssetSnapshot[];
  [key: string]: unknown;
}

export interface GenerationRequest {
  workflow_id?: string | null;
  mode?: GenerationMode | "AUTO" | null;
  quality_profile?: QualityProfile;
  aspect_ratio?: GenerationAspectRatio;
  seed_policy?: SeedPolicy;
  operation?: GenerationOperation;
  parent_generation_id?: string | null;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
  source_video_asset_id?: string | null;
  reference_image_asset_ids?: string[];
  reference_audio_asset_ids?: string[];
  reference_video_asset_ids?: string[];
  execution_prompt?: string | null;
  source_scene_revision?: number | null;
  source_video_revision?: number | null;
  seed?: number | null;
  width?: number | null;
  height?: number | null;
  steps?: number;
  cfg?: number;
  fps?: 24;
  frames?: number;
  audio_policy?: DirectorAudioPolicy;
  motion_context?: MotionContextSettings;
  refine?: RefineSettings;
  face_refine?: FaceRefineSettings;
  audio?: DirectorAudioSettings;
}

export interface GenerateAllRequest {
  settings?: GenerationRequest;
  scene_ids?: string[] | null;
  expected_video_revision?: number;
  expected_scene_revisions?: Record<string, number>;
}

export interface VariationRequest {
  parent_generation_id: string;
  workflow_id?: string | null;
  mode?: GenerationMode | "AUTO" | null;
  quality_profile?: QualityProfile;
  aspect_ratio?: GenerationAspectRatio;
  seed_policy?: SeedPolicy;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
  source_video_asset_id?: string | null;
  reference_image_asset_ids?: string[];
  reference_audio_asset_ids?: string[];
  reference_video_asset_ids?: string[];
  execution_prompt?: string | null;
  source_scene_revision?: number | null;
  source_video_revision?: number | null;
  seed?: number | null;
  width?: number | null;
  height?: number | null;
  steps?: number;
  motion_context?: MotionContextSettings;
  refine?: RefineSettings;
  face_refine?: FaceRefineSettings;
  audio?: DirectorAudioSettings;
}

export interface PromptPreviewDTO {
  raw_prompt: string;
  execution_prompt: string;
  enhanced: boolean;
  warnings: string[];
  scene_revision: number;
  video_revision: number;
}

export interface GenerationDTO {
  id: string;
  video_id: string;
  scene_id: string;
  mode: GenerationMode;
  workflow_id: string;
  generation_no: number;
  operation: GenerationOperation;
  parent_generation_id: string | null;
  status: GenerationStatus | string;
  phase: string | null;
  progress_current: number | null;
  progress_total: number | null;
  revision: number;
  input_snapshot: GenerationInputSnapshot;
  output_metadata: Record<string, unknown>;
  request_id: string | null;
  comfy_prompt_id: string | null;
  output_asset_id: string | null;
  attempt_count: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  queued_at: string | null;
  started_at: string | null;
  progress_updated_at: string | null;
  finished_at: string | null;
}

export interface ExecutionGroupDTO {
  execution_scope: "single_scene" | "aggregate";
  scene_ids: string[];
  generation_ids: string[];
  director_run_id: string | null;
}

export interface BatchGenerationDTO {
  /** Compatibility only: populated when the batch contains exactly one aggregate run. */
  director_run_id?: string | null;
  execution_groups: ExecutionGroupDTO[];
  video_id: string;
  generations: GenerationDTO[];
}

export interface GenerationAttemptDTO {
  id: string;
  generation_id: string;
  attempt_no: number;
  status: string;
  client_id: string;
  comfy_prompt_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface AssemblyRequest {
  delivery_preset?: "SOCIAL_VERTICAL_1080" | "LANDSCAPE_FHD" | "SQUARE_1080" | "PORTRAIT_4_5" | "PORTRAIT_3_4" | "LANDSCAPE_4_3" | "ULTRAWIDE_2560_1080" | null;
  fit_mode?: "FIT_PAD" | "CENTER_CROP";
  transition?: "CUT" | "CROSSFADE";
  crossfade_seconds?: number;
  audio_mode?: "KEEP_SCENE_AUDIO" | "MUTE_SCENE_AUDIO";
  background_audio_asset_id?: string | null;
  background_volume?: number;
  width?: number;
  height?: number;
  fps?: 24 | 25 | 30;
}

export interface FinalDTO {
  id: string;
  video_id: string;
  version_no: number;
  status: string;
  manifest: Record<string, unknown>;
  manifest_hash: string;
  assembly_config: Record<string, unknown>;
  output_asset_id: string | null;
  revision: number;
  phase: string;
  progress_current: number;
  progress_total: number;
  attempt_count: number;
  request_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  progress_updated_at: string | null;
  finished_at: string | null;
}

export interface DashboardSummaryDTO {
  projects: number;
  videos: number;
  assets_ready: number;
  generations_pending: number;
  generations_running: number;
  generations_failed: number;
  assemblies_pending: number;
}

export interface RuntimeComponentDTO {
  healthy: boolean;
  details?: Record<string, unknown>;
}

export interface SystemStatusDTO {
  status: "ok" | "degraded";
  postgres: RuntimeComponentDTO;
  minio: RuntimeComponentDTO;
  comfyui: RuntimeComponentDTO;
  local_storage: RuntimeComponentDTO;
}

export interface StorageSummaryDTO {
  database_assets: number;
  database_bytes: number;
  object_count: number;
  object_bytes: number;
  pending_assets: number;
  failed_assets: number;
  deleted_assets: number;
  local_free_bytes: number;
}

export interface CleanupRequest {
  dry_run?: boolean;
}

export interface CleanupResultDTO {
  dry_run: boolean;
  candidates: unknown[];
  deleted_objects: number;
  retained_records: number;
  has_more: boolean;
}

export interface ReconciliationDTO {
  missing_objects: string[];
  corrupt_objects: string[];
  unavailable_objects: string[];
  orphan_objects: string[];
  repaired_assets: string[];
}

export interface WorkflowCreate {
  execution_scope?: "single_scene" | "aggregate";
  code: string;
  mode: GenerationMode;
  version: string;
  workflow: Record<string, unknown>;
  slots: Record<string, [string, string]>;
  required_slots?: string[];
  profile?: Record<string, unknown>;
}

export interface WorkflowDTO extends WorkflowCreate {
  execution_scope: "single_scene" | "aggregate";
  id: string;
  workflow_hash: string;
  slot_map_hash: string;
  enabled: boolean;
  created_at: string;
  approved_at: string | null;
}

export interface WorkflowApproval {
  enabled: boolean;
}

export interface ErrorDetails {
  code: string;
  message: string;
  trace_id: string;
  details?: Record<string, unknown> | unknown[];
}

export interface ErrorEnvelope {
  error: ErrorDetails;
}
