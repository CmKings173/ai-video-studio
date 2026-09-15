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
export type AspectRatio = "9:16" | "16:9" | "1:1";

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
  enabled: boolean;
  revision: number;
  selected_generation_id: string | null;
  created_at: string;
}

export interface SceneCreate {
  scene_order?: number;
  prompt: string;
  negative_prompt?: string;
  duration_seconds?: number;
  spec?: SceneSpec;
}

export interface ScenePatch {
  prompt?: string;
  negative_prompt?: string;
  duration_seconds?: number;
  spec?: SceneSpec;
  enabled?: boolean;
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
  created_at: string;
}

export interface AssetComplete {
  checksum_sha256?: string | null;
}

export interface DownloadDTO {
  url: string;
  expires_in: number;
}

export type GenerationMode = "t2v" | "i2v" | "i2v_first_last" | "r2v";
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
  assets?: GenerationInputAssetSnapshot[];
  [key: string]: unknown;
}

export interface GenerationRequest {
  workflow_id?: string | null;
  mode?: GenerationMode | null;
  operation?: GenerationOperation;
  parent_generation_id?: string | null;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
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
}

export interface GenerateAllRequest {
  settings?: GenerationRequest;
  scene_ids?: string[] | null;
}

export interface VariationRequest {
  parent_generation_id: string;
  workflow_id?: string | null;
  mode?: GenerationMode | null;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
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

export interface BatchGenerationDTO {
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
  code: string;
  mode: GenerationMode;
  version: string;
  workflow: Record<string, unknown>;
  slots: Record<string, [string, string]>;
  required_slots?: string[];
  profile?: Record<string, unknown>;
}

export interface WorkflowDTO extends WorkflowCreate {
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
