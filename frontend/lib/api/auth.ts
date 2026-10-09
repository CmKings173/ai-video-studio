import { apiClient, persistCsrfToken } from "./client";
import type { LoginDTO, LoginRequest, UserDTO } from "./types";

export async function login(payload: LoginRequest): Promise<LoginDTO> {
  const result = await apiClient.post<LoginDTO>("/api/v1/auth/login", payload);
  if (result?.csrf_token) {
    persistCsrfToken(result.csrf_token);
  }
  return result;
}

export async function logout(): Promise<void> {
  await apiClient.post<void>("/api/v1/auth/logout");
}

export async function getMe(): Promise<UserDTO> {
  return apiClient.get<UserDTO>("/api/v1/auth/me");
}
