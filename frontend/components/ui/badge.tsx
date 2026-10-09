"use client";

import React from "react";
import { useI18n } from "@/lib/i18n";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  status?: string;
  variant?: "default" | "success" | "warning" | "danger" | "purple" | "muted";
}

export function Badge({ status, variant, className = "", children, ...props }: BadgeProps) {
  const { t } = useI18n();
  const normStatus = (status || "").toLowerCase().replace(/_/g, "");
  const names: Record<string, string> = {
    ready: t("Sẵn sàng", "Ready"), completed: t("Hoàn tất", "Completed"), ok: t("Ổn định", "OK"),
    generating: t("Đang tạo", "Generating"), created: t("Đã tạo", "Created"), running: t("Đang chạy", "Running"),
    queued: t("Đang chờ", "Queued"), dispatching: t("Đang gửi", "Dispatching"), collecting: t("Đang nhận", "Collecting"),
    cancelrequested: t("Đang hủy", "Cancelling"), assembling: t("Đang ghép", "Assembling"), validating: t("Đang kiểm tra", "Validating"),
    degraded: t("Suy giảm", "Degraded"), failed: t("Thất bại", "Failed"), error: t("Lỗi", "Error"), cancelled: t("Đã hủy", "Cancelled"),
    dirty: t("Cần cập nhật", "Outdated"), draft: t("Bản nháp", "Draft"), archived: t("Đã lưu trữ", "Archived"),
    admin: t("Quản trị", "Admin"), editor: t("Biên tập", "Editor"), viewer: t("Chỉ xem", "Viewer"),
  };

  let resolvedVariant = variant;
  if (!resolvedVariant && status) {
    if (["ready", "completed", "ok"].includes(normStatus)) {
      resolvedVariant = "success";
    } else if (
      [
        "generating",
        "created",
        "running",
        "queued",
        "dispatching",
        "collecting",
        "cancelrequested",
        "assembling",
        "validating",
        "degraded",
      ].includes(normStatus)
    ) {
      resolvedVariant = "warning";
    } else if (["failed", "error", "cancelled"].includes(normStatus)) {
      resolvedVariant = "danger";
    } else if (["dirty"].includes(normStatus)) {
      resolvedVariant = "purple";
    } else {
      resolvedVariant = "muted";
    }
  }

  const variantStyles = {
    default: "border-[#2c3038] bg-[#22252b] text-[#c3c6d7]",
    success: "border-[#16a34a] bg-green-600/10 text-[#16a34a]",
    warning: "border-[#d97706] bg-amber-600/10 text-[#d97706]",
    danger: "border-[#dc2626] bg-red-600/10 text-[#dc2626]",
    purple: "border-purple-500/40 bg-purple-500/10 text-purple-400",
    muted: "border-[#6b7280] bg-gray-500/10 text-[#9ea5b0]",
  };

  const currentVariant = resolvedVariant || "default";

  return (
    <span
      className={`inline-flex shrink-0 whitespace-nowrap min-h-[22px] items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${variantStyles[currentVariant]} ${className}`}
      {...props}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {children || names[normStatus] || status}
    </span>
  );
}
