"use client";

import React from "react";
import { useI18n } from "@/lib/i18n";
import { validationCopy } from "@/lib/validation-copy";

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  error?: string;
  label?: string;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className = "", error, label, id, ...props }, ref) => {
    const { locale } = useI18n();
    const generatedId = React.useId();
    const fieldId = id ?? generatedId;
    const errorId = `${fieldId}-error`;
    const describedBy = error
      ? [props["aria-describedby"], errorId].filter(Boolean).join(" ")
      : props["aria-describedby"];

    return (
      <div className="flex flex-col self-start gap-1.5 w-full">
        {label && (
          <label htmlFor={fieldId} className="text-xs font-medium text-[#9ea5b0]">
            {label}
          </label>
        )}
        <input
          id={fieldId}
          ref={ref}
          className={`w-full rounded-md border bg-[#181a1e] px-3 py-2 text-sm text-[#f1f3f5] placeholder-[#606773] transition-colors focus:outline-none min-h-[36px] ${
            error
              ? "border-red-500/80 focus:border-red-500"
              : "border-[#2c3038] focus:border-blue-600"
          } ${className}`}
          {...props}
          aria-invalid={error ? true : props["aria-invalid"]}
          aria-describedby={describedBy}
        />
        {error && <p id={errorId} className="text-xs text-red-400 mt-0.5">{validationCopy(error, locale)}</p>}
      </div>
    );
  }
);

Input.displayName = "Input";
