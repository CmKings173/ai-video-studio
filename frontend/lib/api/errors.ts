import type { ErrorEnvelope } from "./types";
import { translateText as t } from "@/lib/i18n";

export class ApiClientError extends Error {
  public readonly code: string;
  public readonly status: number;
  public readonly traceId: string;
  public readonly details: Record<string, unknown> | unknown[];

  constructor(status: number, envelope?: Partial<ErrorEnvelope> | null, fallbackMessage?: string) {
    const code = envelope?.error?.code ?? (status === 401 ? "AUTH_REQUIRED" : "API_ERROR");
    const message =
      envelope?.error?.message ??
      fallbackMessage ??
      `API request failed with status ${status}`;
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.traceId = envelope?.error?.trace_id ?? "";
    this.details = envelope?.error?.details ?? {};
  }
}

export function isRevisionConflict(error: unknown): boolean {
  return (
    error instanceof ApiClientError &&
    error.code === "REVISION_CONFLICT"
  );
}

export function isPreconditionRequired(error: unknown): boolean {
  return (
    error instanceof ApiClientError &&
    (error.status === 428 || error.code === "PRECONDITION_REQUIRED")
  );
}

export function isAuthError(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 401;
}

export function isForbidden(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 403;
}

export function isRateLimited(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 429;
}

export function isValidationError(error: unknown): boolean {
  return error instanceof ApiClientError && error.status === 422;
}

export function getErrorMessage(error: unknown, fallback = "Đã xảy ra lỗi không xác định"): string {
  if (error instanceof ApiClientError) {
    if (error.code === "REVISION_CONFLICT") {
      return t("Dữ liệu đã bị thay đổi bởi người khác hoặc phiên làm việc khác. Vui lòng tải lại và thử lại.", "Data was changed by another user or session. Reload and try again.");
    }
    if (error.code === "LOGIN_RATE_LIMITED") {
      return t("Quá nhiều lần đăng nhập không thành công. Vui lòng thử lại sau.", "Too many unsuccessful sign-in attempts. Try again later.");
    }
    if (error.code === "INVALID_CREDENTIALS") {
      return t("Email hoặc mật khẩu không chính xác.", "Incorrect email or password.");
    }
    if (error.code === "ACCOUNT_DISABLED") {
      return t("Tài khoản của bạn hiện đã bị vô hiệu hoá.", "Your account has been disabled.");
    }
    if (error.message) {
      return error.message;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return fallback === "Đã xảy ra lỗi không xác định" ? t(fallback, "An unexpected error occurred") : fallback;
}
