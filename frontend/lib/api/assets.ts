import { apiClient } from "./client";
import type {
  AssetComplete,
  AssetDTO,
  DownloadDTO,
  Page,
  UploadDTO,
  UploadRequest,
} from "./types";

export type ListAssetsParams = {
  page?: number;
  size?: number;
  project_id?: string;
  product_id?: string;
  status?: string;
  kind?: string;
};

export async function listAssets(params?: ListAssetsParams): Promise<Page<AssetDTO>> {
  return apiClient.get<Page<AssetDTO>>("/api/v1/assets", { params });
}

export async function createUploadUrl(
  payload: UploadRequest,
  idempotencyKey?: string
): Promise<UploadDTO> {
  return apiClient.post<UploadDTO>("/api/v1/assets/upload-url", payload, {
    idempotencyKey,
  });
}

export async function uploadBytesToStorage(
  upload: UploadDTO["upload"],
  file: File
): Promise<void> {
  if (upload?.completed) {
    return;
  }
  if (!upload?.url || !upload?.fields) {
    throw new Error("Invalid upload credentials returned by server");
  }
  const formData = new FormData();
  // S3 / MinIO presigned POST requires fields to come first
  for (const [key, value] of Object.entries(upload.fields)) {
    formData.append(key, value);
  }
  // File must be the last field
  formData.append("file", file);

  const response = await fetch(upload.url, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Upload to storage failed (${response.status}): ${errorText}`);
  }
}

export async function completeAsset(
  assetId: string,
  payload: AssetComplete = {}
): Promise<AssetDTO> {
  return apiClient.post<AssetDTO>(`/api/v1/assets/${assetId}/complete`, payload);
}

export async function getAsset(assetId: string): Promise<AssetDTO> {
  return apiClient.get<AssetDTO>(`/api/v1/assets/${assetId}`);
}

export async function downloadAsset(assetId: string): Promise<DownloadDTO> {
  return apiClient.get<DownloadDTO>(`/api/v1/assets/${assetId}/download`);
}
