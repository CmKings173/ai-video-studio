import { apiClient } from "./client";
import type {
  CleanupRequest,
  CleanupResultDTO,
  Page,
  ReconciliationDTO,
  StorageSummaryDTO,
  SystemStatusDTO,
  UserCreateRequest,
  UserDTO,
  UserPatchRequest,
  WorkflowApproval,
  WorkflowCreate,
  WorkflowDTO,
} from "./types";

export async function listUsers(params?: { page?: number; size?: number }): Promise<Page<UserDTO>> {
  return apiClient.get<Page<UserDTO>>("/api/v1/admin/users", { params });
}

export async function createUser(payload: UserCreateRequest): Promise<UserDTO> {
  return apiClient.post<UserDTO>("/api/v1/admin/users", payload);
}

export async function patchUser(userId: string, payload: UserPatchRequest): Promise<UserDTO> {
  return apiClient.patch<UserDTO>(`/api/v1/admin/users/${userId}`, payload);
}

export async function listWorkflows(params?: {
  page?: number;
  size?: number;
}): Promise<Page<WorkflowDTO>> {
  return apiClient.get<Page<WorkflowDTO>>("/api/v1/admin/workflows", { params });
}

export async function createWorkflow(payload: WorkflowCreate): Promise<WorkflowDTO> {
  return apiClient.post<WorkflowDTO>("/api/v1/admin/workflows", payload);
}

export async function approveWorkflow(
  workflowId: string,
  payload: WorkflowApproval
): Promise<WorkflowDTO> {
  return apiClient.patch<WorkflowDTO>(`/api/v1/admin/workflows/${workflowId}/approval`, payload);
}

export async function getAdminSystemStatus(): Promise<SystemStatusDTO> {
  return apiClient.get<SystemStatusDTO>("/api/v1/admin/system/status");
}

export async function getStorageSummary(): Promise<StorageSummaryDTO> {
  return apiClient.get<StorageSummaryDTO>("/api/v1/admin/storage/summary");
}

export async function cleanupStorage(payload: CleanupRequest = { dry_run: true }): Promise<CleanupResultDTO> {
  return apiClient.post<CleanupResultDTO>("/api/v1/admin/storage/cleanup", payload);
}

export async function reconcileStorage(): Promise<ReconciliationDTO> {
  return apiClient.post<ReconciliationDTO>("/api/v1/admin/storage/reconcile");
}
