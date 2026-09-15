import { ApiClientError } from "./errors";
import type { ErrorEnvelope } from "./types";

const rawBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
export const apiBaseUrl = rawBaseUrl ? rawBaseUrl.replace(/\/+$/, "") : "";

export const CSRF_STORAGE_KEY = "ai_video_studio_csrf_token";
export const AUTH_INVALID_EVENT = "studio:auth-invalid";
let inMemoryCsrfToken: string | null = null;

export function loadCsrfToken(): string | null {
  if (typeof window === "undefined") {
    return inMemoryCsrfToken;
  }
  try {
    const stored = window.localStorage.getItem(CSRF_STORAGE_KEY);
    inMemoryCsrfToken = stored;
    return stored;
  } catch {
    // localStorage might be restricted
  }
  return inMemoryCsrfToken;
}

export function persistCsrfToken(token: string | null): void {
  inMemoryCsrfToken = token;
  if (typeof window === "undefined") {
    return;
  }
  try {
    if (token) {
      window.localStorage.setItem(CSRF_STORAGE_KEY, token);
    } else {
      window.localStorage.removeItem(CSRF_STORAGE_KEY);
    }
  } catch {
    // Ignore storage quota or access errors
  }
}

export function syncCsrfTokenFromStorage(token: string | null): void {
  inMemoryCsrfToken = token;
}

export function clearCsrfToken(): void {
  persistCsrfToken(null);
}

export function setCsrfToken(token: string | null): void {
  persistCsrfToken(token);
}

export function getCsrfToken(): string | null {
  if (!inMemoryCsrfToken) {
    loadCsrfToken();
  }
  return inMemoryCsrfToken;
}

export function generateIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "idemp-" + Math.random().toString(36).substring(2, 15) + Date.now().toString(36);
}

export function apiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${apiBaseUrl}${normalizedPath}`;
}

export interface RequestOptions extends Omit<RequestInit, "body"> {
  params?: Record<string, string | number | boolean | null | undefined>;
  revision?: number;
  idempotencyKey?: string;
  body?: unknown;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { params, revision, idempotencyKey, headers: customHeaders, body, ...restOptions } = options;

  let url = apiUrl(path);

  if (params) {
    const searchParams = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        searchParams.append(key, String(value));
      }
    }
    const queryString = searchParams.toString();
    if (queryString) {
      url += (url.includes("?") ? "&" : "?") + queryString;
    }
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((customHeaders as Record<string, string>) || {}),
  };

  const method = (restOptions.method || "GET").toUpperCase();

  // Attach CSRF token for mutations
  const activeCsrf = getCsrfToken();
  if (activeCsrf && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = activeCsrf;
  }

  // Attach optimistic concurrency revision
  if (revision !== undefined && revision !== null) {
    headers["If-Match"] = `"${revision}"`;
  }

  // Attach Idempotency Key
  if (idempotencyKey) {
    headers["Idempotency-Key"] = idempotencyKey;
  }

  let requestBody: BodyInit | undefined;
  if (body !== undefined && body !== null) {
    if (body instanceof FormData) {
      requestBody = body;
      // Let browser set multipart content-type with boundary
      delete headers["Content-Type"];
    } else {
      headers["Content-Type"] = "application/json";
      requestBody = JSON.stringify(body);
    }
  }

  const response = await fetch(url, {
    ...restOptions,
    method,
    headers,
    credentials: "include",
    body: requestBody,
  });

  if (response.status === 204) {
    return undefined as unknown as T;
  }

  const contentType = response.headers.get("content-type");
  let data: unknown = null;
  if (contentType && contentType.includes("application/json")) {
    try {
      data = await response.json();
    } catch {
      data = null;
    }
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    const errorEnvelope = typeof data === "object" && data !== null && "error" in data
      ? (data as ErrorEnvelope)
      : null;
    const code = errorEnvelope?.error?.code;
    const isAuthEndpoint = path.startsWith("/api/v1/auth/");
    if (typeof window !== "undefined" && !isAuthEndpoint && (response.status === 401 || code === "ACCOUNT_DISABLED")) {
      window.dispatchEvent(new Event(AUTH_INVALID_EVENT));
    }
    throw new ApiClientError(response.status, errorEnvelope, typeof data === "string" ? data : undefined);
  }

  return data as T;
}

export const apiClient = {
  get: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "GET" }),

  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "POST", body }),

  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    request<T>(path, { ...options, method: "PATCH", body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, "method">) =>
    request<T>(path, { ...options, method: "DELETE" }),
};
