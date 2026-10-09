import { queryOptions } from "@tanstack/react-query";
import { apiClient } from "./client";
import { paginationParams } from "./pagination";
import type { AssetComplete, AssetDTO, AssetRole, DownloadDTO, Page, UploadDTO, UploadRequest } from "./types";

export type ListAssetsParams = { page?: number; size?: number; project_id?: string; product_id?: string; status?: string; kind?: string };

export async function listAssets(params?: ListAssetsParams): Promise<Page<AssetDTO>> {
  return apiClient.get<Page<AssetDTO>>("/api/v1/assets", { params: paginationParams(params) });
}

export async function createUploadUrl(payload: UploadRequest, idempotencyKey?: string): Promise<UploadDTO> {
  return apiClient.post<UploadDTO>("/api/v1/assets/upload-url", payload, { idempotencyKey });
}

export async function uploadBytesToStorage(upload: UploadDTO["upload"], file: File): Promise<void> {
  if (upload?.completed) return;
  if (!upload?.url || !upload?.fields) throw new Error("Invalid upload credentials returned by server");
  const formData = new FormData();
  // S3 / MinIO presigned POST requires fields to come first and file last.
  for (const [key, value] of Object.entries(upload.fields)) formData.append(key, value);
  formData.append("file", file);
  const response = await fetch(upload.url, { method: "POST", body: formData });
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Upload to storage failed (${response.status}): ${errorText}`);
  }
}

// Completion acknowledges validation; callers must observe READY before claiming success.
export async function completeAsset(assetId: string, payload: AssetComplete = {}, signal?: AbortSignal): Promise<AssetDTO> {
  return apiClient.post<AssetDTO>(`/api/v1/assets/${assetId}/complete`, payload, ...(signal ? [{ signal }] : []));
}

export async function getAsset(assetId: string, signal?: AbortSignal): Promise<AssetDTO> {
  return apiClient.get<AssetDTO>(`/api/v1/assets/${assetId}`, { signal });
}

export async function downloadAsset(assetId: string): Promise<DownloadDTO> {
  return apiClient.get<DownloadDTO>(`/api/v1/assets/${assetId}/download`);
}

export interface UploadPolicy {
  max_upload_bytes: number;
  allowed_content_types: string[];
}

export async function getUploadPolicy(): Promise<UploadPolicy> {
  const policy = await apiClient.get<UploadPolicy>("/api/v1/assets/upload-policy");
  if (!policy || !Number.isSafeInteger(policy.max_upload_bytes) || policy.max_upload_bytes <= 0
    || !Array.isArray(policy.allowed_content_types) || !policy.allowed_content_types.length
    || !policy.allowed_content_types.every((type) => typeof type === "string" && /^[\w.+-]+\/[\w.+-]+$/.test(type))) {
    throw new Error("Cấu hình tải lên không hợp lệ.");
  }
  return policy;
}

const roleContentTypes: Record<AssetRole, readonly string[]> = {
  PRODUCT_IMAGE: ["image/png", "image/jpeg", "image/webp"],
  PROJECT_REFERENCE: ["image/png", "image/jpeg", "image/webp"],
  SOURCE_VIDEO: ["video/mp4", "video/webm", "video/quicktime"],
  REFERENCE_VIDEO: ["video/mp4", "video/webm", "video/quicktime"],
  REFERENCE_AUDIO: ["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"],
  BACKGROUND_AUDIO: ["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"],
};

export function isAssetRoleCompatible(role: AssetRole, contentType: string): boolean {
  return roleContentTypes[role]?.includes(contentType) ?? false;
}

export function defaultAssetRole(
  contentType: string,
  scope: "project" | "product",
): AssetRole {
  if (contentType.startsWith("image/")) {
    return scope === "product" ? "PRODUCT_IMAGE" : "PROJECT_REFERENCE";
  }
  if (contentType.startsWith("video/")) return "REFERENCE_VIDEO";
  if (contentType.startsWith("audio/")) return "BACKGROUND_AUDIO";
  return "PROJECT_REFERENCE";
}

export function validateUploadFiles(files: ArrayLike<File>, policy: UploadPolicy): string | null {
  if (files.length !== 1) return "Vui lòng chọn đúng một file.";
  const file = files[0];
  if (!policy.allowed_content_types.includes(file.type)) return "Định dạng file không được hỗ trợ.";
  if (file.size <= 0) return "File rỗng không thể tải lên.";
  if (file.size > policy.max_upload_bytes) return `File vượt quá giới hạn ${(policy.max_upload_bytes / 1024 ** 2).toFixed(0)} MiB.`;
  return null;
}

export const ASSET_VALIDATION_TIMEOUT_MS = 120_000;

// Each session has its own request budget; only GET polling observes validation.
export function assetValidationQueryOptions(asset: AssetDTO | null, startedAt: number) {
  return queryOptions({
    queryKey: ["asset-upload-validation", asset?.id, startedAt],
    queryFn: ({ signal }) => getAsset(asset!.id, signal),
    initialData: asset ?? undefined,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchInterval: (query) => query.state.data?.status === "VALIDATING"
      && query.state.status !== "error" && query.state.dataUpdateCount < 60
      && Date.now() - startedAt < ASSET_VALIDATION_TIMEOUT_MS ? 2_000 : false,
  });
}
