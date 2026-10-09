"use client";

import React from "react";

export interface ProgressProps extends React.HTMLAttributes<HTMLDivElement> {
  value?: number | null;
  max?: number;
  label?: string;
}

export function Progress({ value = 0, max = 100, label, className = "", ...props }: ProgressProps) {
  const percentage = Math.min(100, Math.max(0, Math.round(((value || 0) / max) * 100)));

  return (
    <div className={`w-full space-y-1.5 ${className}`} {...props}>
      {label && (
        <div className="flex justify-between text-xs font-semibold text-[#9ea5b0]">
          <span>{label}</span>
          <span>{percentage}%</span>
        </div>
      )}
      <div className="h-2 w-full overflow-hidden rounded-md border border-[#2c3038] bg-[#0c0e12]">
        <div
          className="h-full bg-blue-600 transition-all duration-300 ease-out"
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}
