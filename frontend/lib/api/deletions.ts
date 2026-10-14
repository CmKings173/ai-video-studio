import { apiClient } from "./client";

export type DeletionResourceType = "video" | "scene" | "generation" | "final-version";

export interface DeletionPreview {
  resource_type: string;
  resource_id: string;
  name?: string | null;
  revision?: number;
  allowed: boolean;
  reason?: string | null;
  blockers?: unknown[];
  affected_counts?: Record<string, unknown>;
  shared_assets_preserved?: boolean | number | null;
  cleanup_eligible_outputs?: unknown;
  cleanup_blocked_outputs?: unknown;
  current?: unknown;
  async_cleanup?: boolean;
  confirmation_token?: string | null;
  [key: string]: unknown;
}

export interface DeletionOperation {
  id: string;
  status: string;
  resource_type: string;
  resource_id: string;
  cleanup_progress?: number | Record<string, unknown> | null;
  cleanup_progress_percent?: number | null;
  cleanup_status?: string | null;
  cleanup_error_code?: string | null;
  next_attempt_at?: string | null;
  error_code?: string | null;
  [key: string]: unknown;
}

export type DeletionAccepted = DeletionOperation | { operation?: DeletionOperation } | null;

function resourcePath(type: DeletionResourceType, id: string): string {
  const collection = type === "final-version" ? "final-versions" : type === "video" ? "videos"
    : type === "scene" ? "scenes" : "generations";
  return "/api/v1/" + collection + "/" + encodeURIComponent(id);
}

export async function getDeletionPreview(
  type: DeletionResourceType,
  id: string
): Promise<DeletionPreview> {
  return apiClient.get<DeletionPreview>(resourcePath(type, id) + "/deletion-preview");
}

export async function deleteResource(
  type: DeletionResourceType,
  id: string,
  payload: { expected_revision: number; confirmation_token?: string }
): Promise<DeletionAccepted> {
  const result = await apiClient.delete<DeletionAccepted>(resourcePath(type, id), { body: payload });
  return result ?? null;
}

export async function getDeletionOperation(operationId: string): Promise<DeletionOperation> {
  return apiClient.get<DeletionOperation>("/api/v1/deletion-operations/" + encodeURIComponent(operationId));
}
