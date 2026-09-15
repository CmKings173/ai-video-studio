import { apiClient } from "./client";
import type { ReorderRequest, SceneCreate, SceneDTO, ScenePatch, SelectionRequest } from "./types";

export async function listScenes(videoId: string): Promise<SceneDTO[]> {
  return apiClient.get<SceneDTO[]>(`/api/v1/videos/${videoId}/scenes`);
}

export async function getScene(sceneId: string): Promise<SceneDTO> {
  return apiClient.get<SceneDTO>(`/api/v1/scenes/${sceneId}`);
}

export async function createScene(
  videoId: string,
  payload: SceneCreate,
  videoRevision: number
): Promise<SceneDTO> {
  return apiClient.post<SceneDTO>(`/api/v1/videos/${videoId}/scenes`, payload, {
    revision: videoRevision,
  });
}

export async function patchScene(
  sceneId: string,
  payload: ScenePatch,
  revision: number
): Promise<SceneDTO> {
  return apiClient.patch<SceneDTO>(`/api/v1/scenes/${sceneId}`, payload, { revision });
}

export async function reorderScenes(
  videoId: string,
  payload: ReorderRequest,
  videoRevision: number
): Promise<SceneDTO[]> {
  return apiClient.post<SceneDTO[]>(`/api/v1/videos/${videoId}/scenes/reorder`, payload, {
    revision: videoRevision,
  });
}

export async function selectGeneration(
  sceneId: string,
  payload: SelectionRequest,
  revision: number
): Promise<SceneDTO> {
  return apiClient.post<SceneDTO>(`/api/v1/scenes/${sceneId}/select-generation`, payload, {
    revision,
  });
}

export async function enableScene(sceneId: string, revision: number): Promise<SceneDTO> {
  return apiClient.post<SceneDTO>(`/api/v1/scenes/${sceneId}/enable`, undefined, { revision });
}

export async function disableScene(sceneId: string, revision: number): Promise<SceneDTO> {
  return apiClient.post<SceneDTO>(`/api/v1/scenes/${sceneId}/disable`, undefined, { revision });
}
