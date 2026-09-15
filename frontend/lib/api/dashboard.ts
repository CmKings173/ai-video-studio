import { apiClient } from "./client";
import type { DashboardSummaryDTO, SystemStatusDTO } from "./types";

export async function getDashboardSummary(): Promise<DashboardSummaryDTO> {
  return apiClient.get<DashboardSummaryDTO>("/api/v1/dashboard/summary");
}

export async function getSystemStatus(): Promise<SystemStatusDTO> {
  return apiClient.get<SystemStatusDTO>("/api/v1/system/status");
}
