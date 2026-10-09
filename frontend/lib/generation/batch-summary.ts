import type { VideoDetail } from "@/lib/api/types";
import { deriveMode } from "./capabilities";
import { eligibleScenes } from "./eligible-scenes";

export function batchSummary(video: VideoDetail) {
  return eligibleScenes(video.scenes).map((scene) => {
      const config = scene.generation_config ?? {};
      return { id: scene.id, order: scene.scene_order + 1, seconds: scene.duration_seconds,
        mode: config.mode && config.mode !== "AUTO" ? config.mode : deriveMode(config),
        quality: config.quality_profile ?? "STANDARD", ratio: config.aspect_ratio ?? video.aspect_ratio,
        seed: config.seed_policy === "FIXED" ? config.seed : null };
    });
}
