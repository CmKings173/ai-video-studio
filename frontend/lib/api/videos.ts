import { apiClient } from "./client";
import type {
  Page,
  StoryboardPreviewDTO,
  StoryboardPublishRequest,
  VideoCreate,
  VideoDTO,
  VideoDetail,
  VideoPatch,
} from "./types";

export type ListVideosParams = {
  page?: number;
  size?: number;
  search?: string;
  project_id?: string;
  product_id?: string;
  status?: string;
  kind?: string;
};

export async function listVideos(params?: ListVideosParams): Promise<Page<VideoDTO>> {
  return apiClient.get<Page<VideoDTO>>("/api/v1/videos", { params });
}

export async function createVideo(payload: VideoCreate): Promise<VideoDetail> {
  return apiClient.post<VideoDetail>("/api/v1/videos", payload);
}

export async function getVideo(videoId: string): Promise<VideoDetail> {
  return apiClient.get<VideoDetail>(`/api/v1/videos/${videoId}`);
}

export async function patchVideo(
  videoId: string,
  payload: VideoPatch,
  revision: number
): Promise<VideoDTO> {
  return apiClient.patch<VideoDTO>(`/api/v1/videos/${videoId}`, payload, { revision });
}

export async function previewStoryboard(videoId: string): Promise<StoryboardPreviewDTO> {
  return apiClient.post<StoryboardPreviewDTO>(`/api/v1/videos/${videoId}/storyboard/preview`);
}

export async function publishStoryboard(
  videoId: string,
  payload: StoryboardPublishRequest,
  revision: number,
  idempotencyKey?: string
): Promise<VideoDetail> {
  return apiClient.post<VideoDetail>(`/api/v1/videos/${videoId}/storyboard/publish`, payload, {
    revision,
    idempotencyKey,
  });
}
