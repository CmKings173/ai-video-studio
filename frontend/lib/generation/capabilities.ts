import { translateText as t } from "@/lib/i18n";
import type { AssetDTO, GenerationCapabilitiesDTO, GenerationCapability, GenerationMode, GenerationRequest } from "@/lib/api/types";

export function qualifiedCombinations(caps: GenerationCapabilitiesDTO | undefined): GenerationCapability[] {
  if (!caps) return [];
  if (Array.isArray(caps.qualified_capabilities)) return caps.qualified_capabilities;
  if (caps.qualified_capabilities && Array.isArray(caps.qualified_capabilities.combinations)) {
    return caps.qualified_capabilities.combinations;
  }
  return caps.combinations ?? [];
}

export function deriveMode(input: Pick<GenerationRequest,
  "first_frame_asset_id" | "last_frame_asset_id" | "source_video_asset_id" |
  "reference_image_asset_ids" | "reference_video_asset_ids" | "reference_audio_asset_ids">): GenerationMode {
  const hasReferences = Boolean(input.reference_image_asset_ids?.length || input.reference_video_asset_ids?.length || input.reference_audio_asset_ids?.length);
  if (input.source_video_asset_id) return hasReferences ? "rv2v" : "v2v";
  if (hasReferences) return "r2v";
  if (input.first_frame_asset_id && input.last_frame_asset_id) return "i2v_first_last";
  if (input.first_frame_asset_id) return "i2v";
  if (input.last_frame_asset_id) return "i2v_last";
  return "t2v";
}

/** The editor and execution resolver use the same qualified scope/mode/profile/ratio filter. */
export function matchingQualifiedCapabilities(caps: GenerationCapabilitiesDTO | undefined, input: GenerationRequest, scope: "single_scene" | "aggregate", matchCustomDimensions = true): GenerationCapability[] {
  const mode = input.mode && input.mode !== "AUTO" ? input.mode : deriveMode(input);
  return caps?.available ? qualifiedCombinations(caps).filter((item) => (item.mode === mode
    || (item.mode === "fl2v" && ["i2v_last", "i2v_first_last"].includes(mode)))
    && item.quality_profile === (input.quality_profile ?? "STANDARD")
    && item.aspect_ratio === input.aspect_ratio
    && item.execution_scope === scope
    && (input.aspect_ratio !== "Custom" || !matchCustomDimensions || (input.width === item.resolved_width && input.height === item.resolved_height))
    && (input.workflow_id == null || item.workflow_id === input.workflow_id)) : [];
}

function capabilityForScope(caps: GenerationCapabilitiesDTO | undefined, input: GenerationRequest, scope: "single_scene" | "aggregate"): GenerationCapability | null {
  return matchingQualifiedCapabilities(caps, input, scope)[0] ?? null;
}

export function singleSceneCapability(caps: GenerationCapabilitiesDTO | undefined, input: GenerationRequest): GenerationCapability | null {
  return capabilityForScope(caps, input, "single_scene");
}

export function aggregateCapability(caps: GenerationCapabilitiesDTO | undefined, input: GenerationRequest): GenerationCapability | null {
  return capabilityForScope(caps, input, "aggregate");
}

export const matchingCapability = singleSceneCapability;

/** Source support flags alone never qualify an executable Director feature. */
export function qualifiedDirectorFeature(cap: GenerationCapability | null, feature: "refine" | "face_refine" | "motion_context"): boolean {
  return cap?.provider === "minimax_h3_director"
    && cap[`supports_${feature}`] === true
    && cap.director_settings?.some((settings) => settings[feature]?.enabled === true) === true;
}

/** Early editor feedback; the backend independently validates the stored bytes. */
export function generationInputProblems(input: GenerationRequest, cap: GenerationCapability | null, assets: AssetDTO[], customCanvasCapabilities: GenerationCapability[] = []): string[] {
  const problems: string[] = [];
  if (!cap) problems.push(t("Chưa có workflow được xác minh cho chế độ, chất lượng và tỷ lệ này.", "No verified workflow matches this mode, quality and aspect ratio."));
  if (input.aspect_ratio === "Custom") {
    const validDimension = (value: number | null | undefined) => Number.isInteger(value) && (value ?? 0) >= 32 && (value ?? 0) <= 8192 && (value ?? 0) % 32 === 0;
    if (!validDimension(input.width) || !validDimension(input.height)) problems.push(t("Kích thước tùy chỉnh phải từ 32 đến 8192 và chia hết cho 32.", "Custom dimensions must be from 32 to 8192 and divisible by 32."));
    else if (customCanvasCapabilities.length && !customCanvasCapabilities.some((item) => item.resolved_width === input.width && item.resolved_height === input.height)) {
      const sizes = Array.from(new Set(customCanvasCapabilities.map((item) => `${item.resolved_width}×${item.resolved_height}`))).join(", ");
      problems.push(t(`Kích thước tùy chỉnh phải khớp một độ phân giải đã xác minh: ${sizes}.`, `Custom dimensions must match a verified resolution: ${sizes}.`));
    }
  }
  if (!cap) return problems;
  const defaults: Record<string, Record<string, unknown>> = {
    motion_context: { enabled: false, context_frames: 22, audio_context_frames: 24, continuity: true, keep_tail: false },
    refine: { enabled: false, mode: "refine", upscale_method: "h3_latent", passes: 1, seed_mode: "inherit", aspect_ratio: "follow_director", megapixels: 0, width: 0, height: 0, skip_fl2v: true, enable_latent_chunking: false, enable_tiling: false },
    face_refine: { enabled: false, detector: "face_yolov8m.pt", confidence: 0.35, crop_factor: 2.5, canvas_width: 768, canvas_height: 768, canvas_mode: "manual", select: "largest_face", denoise: 0.4, steps: 8, seed_mode: "inherit", paste_region: "face_only", mask_dilation: 16, feather: 24, colour_match: 1, blend: 1 },
    audio_policy: { mode: "generate", preserve_source_audio: true },
  };
  const optional = ["motion_context", "refine", "face_refine", "audio_policy"] as const;
  const special = input.motion_context?.enabled || input.refine?.enabled || input.face_refine?.enabled
    || (input.audio_policy?.mode && input.audio_policy.mode !== "generate");
  if (special && cap.provider === "minimax_h3_director" && !cap.director_settings?.some((candidate) =>
    optional.every((name) => {
      const requested = input[name] as Record<string, unknown> | undefined;
      const qualified = candidate[name] as Record<string, unknown> | undefined;
      const normalized = { ...defaults[name], ...requested };
      return Object.entries(normalized).every(([key, value]) => value === qualified?.[key])
        && Object.keys(qualified ?? {}).every((key) => key in normalized);
    }))) problems.push(t("Các thiết lập Director này chưa có bằng chứng chạy cùng nhau. Chọn cấu hình đã xác minh.", "These Director settings have not been verified together. Choose a verified configuration."));
  const mode = deriveMode(input);
  const compatible = (value: string) => value === mode || (value === "fl2v" && ["i2v_last", "i2v_first_last"].includes(mode));
  if (!compatible(cap.mode) || (input.mode && input.mode !== "AUTO" && !compatible(input.mode))) problems.push(t("Chế độ không khớp tài nguyên đã chọn.", "Mode does not match the selected assets."));
  if (input.motion_context?.enabled && !cap.supports_motion_context) problems.push(t("Ngữ cảnh chuyển động chưa được xác minh cho cấu hình này.", "Motion Context is not qualified for this configuration."));
  if (input.refine?.enabled && !cap.supports_refine) problems.push(t("Tinh chỉnh chưa được xác minh cho cấu hình này.", "Refine is not qualified for this configuration."));
  if (input.face_refine?.enabled && !cap.supports_face_refine) problems.push(t("Tinh chỉnh khuôn mặt chưa được xác minh cho cấu hình này.", "Face Refine is not qualified for this configuration."));
  if (input.audio_policy?.mode && !(cap.audio_modes ?? ["generate"]).includes(input.audio_policy.mode)) problems.push(t("Chính sách âm thanh chưa được xác minh cho cấu hình này.", "Audio policy is not qualified for this configuration."));
  if (input.seed_policy === "FIXED" && (!Number.isSafeInteger(input.seed) || (input.seed ?? -1) < 0)) problems.push(t("Seed cố định phải là số nguyên từ 0 đến 9007199254740991.", "Fixed seed must be an integer from 0 to 9007199254740991."));
  if (!cap) return problems;
  const images = input.reference_image_asset_ids ?? [];
  const videos = input.reference_video_asset_ids ?? [];
  const audio = input.reference_audio_asset_ids ?? [];
  const total = images.length + videos.length + audio.length;
  if (total && (input.first_frame_asset_id || input.last_frame_asset_id)) problems.push(t("Không trộn khung đầu/cuối với tài nguyên tham chiếu.", "Do not mix first/last frames with references."));
  if (input.source_video_asset_id && (input.first_frame_asset_id || input.last_frame_asset_id)) problems.push(t("Video nguồn không dùng cùng khung đầu/cuối.", "Source video cannot be combined with first/last frames."));
  if (input.first_frame_asset_id && input.first_frame_asset_id === input.last_frame_asset_id) problems.push(t("Khung đầu và cuối phải là hai ảnh khác nhau.", "First and last frames must use different images."));
  if (total > cap.max_total_reference_files) problems.push(t(`Workflow hỗ trợ tối đa ${cap.max_total_reference_files} tài nguyên tham chiếu.`, `Workflow supports at most ${cap.max_total_reference_files} references.`));
  if (audio.length && !images.length && !videos.length && cap.audio_requires_visual_reference) problems.push(t("Âm thanh cần ít nhất một ảnh hoặc video tham chiếu.", "Audio requires at least one image or video reference."));

  const kindLabel = (kind: string) => kind === "image" ? t("ảnh", "image") : kind === "audio" ? t("âm thanh", "audio") : t("video", "video");
  function checkAsset(id: string, kind: string): AssetDTO | undefined {
    const asset = assets.find((item) => item.id === id);
    if (!asset || asset.status !== "READY" || !asset.content_type.startsWith(`${kind}/`)) {
      problems.push(t(`Tài nguyên ${id} chưa sẵn sàng hoặc sai loại ${kindLabel(kind)}.`, `Asset ${id} is not READY or has the wrong type (${kind}).`));
      return undefined;
    }
    return asset;
  }
  if (input.first_frame_asset_id) checkAsset(input.first_frame_asset_id, "image");
  if (input.last_frame_asset_id) checkAsset(input.last_frame_asset_id, "image");
  if (input.source_video_asset_id) checkAsset(input.source_video_asset_id, "video");
  const groups = [
    { ids: images, kind: "image", role: "REFERENCE_IMAGE", max: cap.max_reference_images },
    { ids: videos, kind: "video", role: "REFERENCE_VIDEO", max: cap.max_reference_videos },
    { ids: audio, kind: "audio", role: "REFERENCE_AUDIO", max: cap.max_reference_audio },
  ];
  for (const group of groups) {
    if (group.ids.length > group.max) problems.push(t(`Workflow hỗ trợ tối đa ${group.max} tài nguyên ${kindLabel(group.kind)} tham chiếu.`, `Workflow supports at most ${group.max} ${group.kind} references.`));
    if (new Set(group.ids).size !== group.ids.length) problems.push(t(`Tài nguyên ${kindLabel(group.kind)} tham chiếu bị trùng.`, `Duplicate ${group.kind} references.`));
    const nativeCount = cap.required_asset_slots.filter((slot) => slot.startsWith(`${group.role}_`)).length;
    if (nativeCount && group.ids.length !== nativeCount) problems.push(t(`Workflow này cần đúng ${nativeCount} tài nguyên ${kindLabel(group.kind)} tham chiếu.`, `This graph requires exactly ${nativeCount} ${group.kind} references.`));
    let duration = 0;
    for (const id of group.ids) {
      const asset = checkAsset(id, group.kind);
      if (!asset || group.kind === "image") continue;
      const span = asset.media_metadata?.[`${group.kind}_duration_seconds`];
      if (typeof span !== "number" || !Number.isFinite(span) || span < cap.clip_min_seconds || span > cap.clip_max_seconds) {
        problems.push(t(`${asset.filename ?? id}: cần thời lượng ${kindLabel(group.kind)} được đo từ ${cap.clip_min_seconds} đến ${cap.clip_max_seconds} giây.`, `${asset.filename ?? id}: measured ${group.kind} duration must be between ${cap.clip_min_seconds} and ${cap.clip_max_seconds} seconds.`));
      } else duration += span;
      if (group.kind === "video" && asset.media_metadata?.fps !== cap.reference_video_fps) problems.push(t(`${asset.filename ?? id}: video cần chuẩn hóa về ${cap.reference_video_fps} FPS.`, `${asset.filename ?? id}: video must be normalized to ${cap.reference_video_fps} FPS.`));
    }
    if (duration > cap.category_total_max_seconds) problems.push(t(`Tổng thời lượng ${kindLabel(group.kind)} tham chiếu vượt ${cap.category_total_max_seconds} giây.`, `Total ${group.kind} reference duration exceeds ${cap.category_total_max_seconds} seconds.`));
  }
  return problems;
}
