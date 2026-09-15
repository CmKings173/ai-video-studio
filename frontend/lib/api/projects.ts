import { apiClient } from "./client";
import type { AssetDTO, Page, ProjectDTO, ResourceCreate, ResourcePatch, VideoDTO } from "./types";

export type ListProjectsParams = {
  page?: number;
  size?: number;
  search?: string;
  archived?: boolean;
};

export async function listProjects(params?: ListProjectsParams): Promise<Page<ProjectDTO>> {
  return apiClient.get<Page<ProjectDTO>>("/api/v1/projects", { params });
}

export async function createProject(payload: ResourceCreate): Promise<ProjectDTO> {
  return apiClient.post<ProjectDTO>("/api/v1/projects", payload);
}

export async function getProject(projectId: string): Promise<ProjectDTO> {
  return apiClient.get<ProjectDTO>(`/api/v1/projects/${projectId}`);
}

export async function patchProject(
  projectId: string,
  payload: ResourcePatch,
  revision: number
): Promise<ProjectDTO> {
  return apiClient.patch<ProjectDTO>(`/api/v1/projects/${projectId}`, payload, { revision });
}

export async function archiveProject(projectId: string, revision: number): Promise<ProjectDTO> {
  return apiClient.post<ProjectDTO>(`/api/v1/projects/${projectId}/archive`, undefined, { revision });
}

export async function getProjectVideos(
  projectId: string,
  params?: { page?: number; size?: number }
): Promise<Page<VideoDTO>> {
  return apiClient.get<Page<VideoDTO>>(`/api/v1/projects/${projectId}/videos`, { params });
}

export async function getProjectAssets(
  projectId: string,
  params?: { page?: number; size?: number }
): Promise<Page<AssetDTO>> {
  return apiClient.get<Page<AssetDTO>>(`/api/v1/projects/${projectId}/assets`, { params });
}
