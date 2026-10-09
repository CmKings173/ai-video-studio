import { apiClient } from "./client";
import { paginationParams } from "./pagination";
import { eligibleScenes } from "../generation/eligible-scenes";
import type {
  BatchGenerationDTO,
  GenerateAllRequest,
  GenerationAttemptDTO,
  GenerationDTO,
  GenerationCapabilitiesDTO,
  GenerationRequest,
  Page,
  PromptPreviewDTO,
  VariationRequest,
  VideoDetail,
} from "./types";

/** Pin the ordered eligible batch; runtime generation progress is not semantic input. */
export function prepareGenerateAll(video: VideoDetail) {
  const orderedScenes = [...video.scenes]
    .sort((a, b) => a.scene_order - b.scene_order || a.id.localeCompare(b.id));
  const scenes = eligibleScenes(orderedScenes);
  const payload: GenerateAllRequest = {
    scene_ids: scenes.map((scene) => scene.id),
    expected_video_revision: video.revision,
    expected_scene_revisions: Object.fromEntries(scenes.map((scene) => [scene.id, scene.revision])),
  };
  const fingerprint = {
    video_id: video.id,
    video_revision: video.revision,
    video_config: video.config,
    scenes: scenes.map((scene) => ({
      id: scene.id,
      revision: scene.revision,
      prompt: scene.prompt,
      negative_prompt: scene.negative_prompt,
      duration_seconds: scene.duration_seconds,
      spec: scene.spec,
      generation_config: scene.generation_config ?? null,
    })),
    settings: payload.settings ?? {},
  };
  // Revisions and output selection are validated by the backend. They can change
  // during dispatcher completion without any editor action.
  const editorInputs = {
    video_id: video.id,
    video: { title: video.title, brief: video.brief, config: video.config, kind: video.kind,
      aspect_ratio: video.aspect_ratio, target_duration: video.target_duration,
      project_id: video.project_id, product_id: video.product_id, brand_id: video.brand_id },
    scenes: orderedScenes.map((scene) => ({
      id: scene.id, scene_order: scene.scene_order, enabled: scene.enabled,
      prompt: scene.prompt, negative_prompt: scene.negative_prompt,
      duration_seconds: scene.duration_seconds, spec: scene.spec,
      generation_config: scene.generation_config ?? null,
    })),
  };
  return { payload, fingerprint, editorInputs };
}

export async function previewPrompt(sceneId: string, settings?: GenerationRequest): Promise<PromptPreviewDTO> {
  return apiClient.post<PromptPreviewDTO>(`/api/v1/scenes/${sceneId}/prompt-preview`, settings);
}

export async function generationCapabilities(): Promise<GenerationCapabilitiesDTO> {
  return apiClient.get<GenerationCapabilitiesDTO>("/api/v1/generation-capabilities");
}

export async function regenerate(sceneId: string, parentId: string, idempotencyKey?: string): Promise<GenerationDTO> {
  return apiClient.post<GenerationDTO>(`/api/v1/scenes/${sceneId}/regenerations`,
    { parent_generation_id: parentId }, { idempotencyKey });
}

export async function createGeneration(
  sceneId: string,
  payload: GenerationRequest,
  idempotencyKey?: string
): Promise<GenerationDTO> {
  return apiClient.post<GenerationDTO>(`/api/v1/scenes/${sceneId}/generations`, payload, {
    idempotencyKey,
  });
}

export async function createVariation(
  sceneId: string,
  payload: VariationRequest,
  idempotencyKey?: string
): Promise<GenerationDTO> {
  return apiClient.post<GenerationDTO>(`/api/v1/scenes/${sceneId}/variations`, payload, {
    idempotencyKey,
  });
}

export async function generateAll(
  videoId: string,
  payload: GenerateAllRequest,
  idempotencyKey?: string
): Promise<BatchGenerationDTO> {
  return apiClient.post<BatchGenerationDTO>(`/api/v1/videos/${videoId}/generate-all`, payload, {
    idempotencyKey,
  });
}

export async function listSceneGenerations(
  sceneId: string,
  params?: { page?: number; size?: number }
): Promise<Page<GenerationDTO>> {
  return apiClient.get<Page<GenerationDTO>>(`/api/v1/scenes/${sceneId}/generations`, { params: paginationParams(params) });
}

export async function getGeneration(generationId: string): Promise<GenerationDTO> {
  return apiClient.get<GenerationDTO>(`/api/v1/generations/${generationId}`);
}

export async function listGenerationAttempts(
  generationId: string
): Promise<GenerationAttemptDTO[]> {
  return apiClient.get<GenerationAttemptDTO[]>(`/api/v1/generations/${generationId}/attempts`);
}

export async function cancelGeneration(generationId: string): Promise<GenerationDTO> {
  return apiClient.post<GenerationDTO>(`/api/v1/generations/${generationId}/cancel`);
}
