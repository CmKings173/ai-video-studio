import { apiClient } from "./client";
import type {
  BatchGenerationDTO,
  GenerateAllRequest,
  GenerationAttemptDTO,
  GenerationDTO,
  GenerationRequest,
  Page,
  PromptPreviewDTO,
  VariationRequest,
} from "./types";

export async function previewPrompt(sceneId: string): Promise<PromptPreviewDTO> {
  return apiClient.post<PromptPreviewDTO>(`/api/v1/scenes/${sceneId}/prompt-preview`);
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
  return apiClient.get<Page<GenerationDTO>>(`/api/v1/scenes/${sceneId}/generations`, { params });
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
