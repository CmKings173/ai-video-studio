import { apiClient } from "./client";
import type { AssemblyRequest, DownloadDTO, FinalDTO } from "./types";

export async function assembleVideo(
  videoId: string,
  payload: AssemblyRequest,
  videoRevision: number,
  idempotencyKey?: string
): Promise<FinalDTO> {
  return apiClient.post<FinalDTO>(`/api/v1/videos/${videoId}/assemble`, payload, {
    revision: videoRevision,
    idempotencyKey,
  });
}

export async function listFinalVersions(videoId: string): Promise<FinalDTO[]> {
  return apiClient.get<FinalDTO[]>(`/api/v1/videos/${videoId}/final-versions`);
}

export async function getFinalVersion(finalId: string): Promise<FinalDTO> {
  return apiClient.get<FinalDTO>(`/api/v1/final-versions/${finalId}`);
}

export async function downloadFinalVersion(finalId: string): Promise<DownloadDTO> {
  return apiClient.get<DownloadDTO>(`/api/v1/final-versions/${finalId}/download`);
}

export async function cancelFinalVersion(finalId: string): Promise<FinalDTO> {
  return apiClient.post<FinalDTO>(`/api/v1/final-versions/${finalId}/cancel`);
}
