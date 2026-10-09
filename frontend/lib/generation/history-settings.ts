import type { GenerationAspectRatio, GenerationDTO, GenerationMode, QualityProfile, SceneGenerationConfig } from "@/lib/api/types";

/** Reuse carries intent; current qualification must resolve all execution overrides. */
export function reuseGenerationSettings(generation: GenerationDTO): SceneGenerationConfig {
  const snapshot = generation.input_snapshot;
  const director = snapshot.director_execution_spec;
  const frozenDirector = snapshot.director_execution;
  const mode = snapshot.mode ?? generation.mode;
  const quality = snapshot.requested_quality_profile;
  const ratio = snapshot.requested_aspect_ratio;
  const seed = snapshot.seed_policy === "FIXED" && typeof snapshot.seed === "number" && Number.isSafeInteger(snapshot.seed) && snapshot.seed >= 0 ? snapshot.seed : null;
  function ids(role: string) {
    const frozen = (snapshot.assets ?? []).filter((asset) => asset.role === role)
      .sort((a, b) => (a.order_index ?? 0) - (b.order_index ?? 0)).map((asset) => asset.id);
    if (frozen.length) return frozen;
    return (director?.inputs ?? []).filter((asset) => asset.role === role && typeof asset.asset_id === "string")
      .sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0)).map((asset) => asset.asset_id as string);
  }
  const motionContext = frozenDirector?.motion_context ?? snapshot.motion_context ?? director?.continuity;
  const refine = frozenDirector?.refine ?? snapshot.refine ?? director?.refine;
  const faceRefine = frozenDirector?.face_refine ?? snapshot.face_refine ?? director?.face_refine;
  const audio = snapshot.audio ?? director?.audio;
  const audioPolicy = frozenDirector?.audio_policy ?? snapshot.audio_policy;
  return {
    mode: ["t2v", "i2v", "fl2v", "i2v_last", "i2v_first_last", "r2v", "v2v", "rv2v"].includes(mode) ? mode as GenerationMode : "AUTO",
    quality_profile: ["DRAFT", "STANDARD", "HIGH", "BASE", "HD", "FULL_HD_REFINED", "CUSTOM"].includes(String(quality)) ? quality as QualityProfile : "STANDARD",
    ...(typeof ratio === "string" && ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"].includes(ratio) ? { aspect_ratio: ratio as GenerationAspectRatio } : {}),
    seed_policy: seed !== null && snapshot.seed_policy === "FIXED" ? "FIXED" : "RANDOM", seed,
    first_frame_asset_id: ids("FIRST_FRAME")[0] ?? null, last_frame_asset_id: ids("LAST_FRAME")[0] ?? null,
    source_video_asset_id: ids("SOURCE_VIDEO")[0] ?? null,
    reference_image_asset_ids: ids("REFERENCE_IMAGE"), reference_video_asset_ids: ids("REFERENCE_VIDEO"), reference_audio_asset_ids: ids("REFERENCE_AUDIO"),
    ...(motionContext ? { motion_context: structuredClone(motionContext) as SceneGenerationConfig["motion_context"] } : {}),
    ...(refine ? { refine: structuredClone(refine) as SceneGenerationConfig["refine"] } : {}),
    ...(faceRefine ? { face_refine: structuredClone(faceRefine) as SceneGenerationConfig["face_refine"] } : {}),
    ...(audioPolicy ? { audio_policy: structuredClone(audioPolicy) } : audio ? { audio_policy: { mode: (audio as { enabled?: boolean }).enabled === false ? "mute" : "generate" } as SceneGenerationConfig["audio_policy"] } : {}),
  };
}
