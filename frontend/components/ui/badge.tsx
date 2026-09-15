"use client";

import React from "react";

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  status?: string;
  variant?: "default" | "success" | "warning" | "danger" | "purple" | "muted";
}

export function Badge({ status, variant, className = "", children, ...props }: BadgeProps) {
  const normStatus = (status || "").toLowerCase().replace(/_/g, "");

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
      className={`inline-flex min-h-[22px] items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium ${variantStyles[currentVariant]} ${className}`}
      {...props}
    >
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {children || status}
    </span>
  );
}
