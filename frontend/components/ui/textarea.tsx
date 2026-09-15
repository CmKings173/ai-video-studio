"use client";

import React from "react";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: string;
  label?: string;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className = "", error, label, id, ...props }, ref) => {
    return (
      <div className="grid gap-1.5 w-full">
        {label && (
          <label htmlFor={id} className="text-xs font-medium text-[#9ea5b0]">
            {label}
          </label>
        )}
        <textarea
          id={id}
          ref={ref}
          className={`w-full rounded-md border bg-[#181a1e] px-3 py-2.5 text-sm text-[#f1f3f5] placeholder-[#606773] transition-colors focus:outline-none min-h-[110px] resize-y ${
            error
              ? "border-red-500/80 focus:border-red-500"
              : "border-[#2c3038] focus:border-blue-600"
          } ${className}`}
          {...props}
        />
        {error && <p className="text-xs text-red-400 mt-0.5">{error}</p>}
      </div>
    );
  }
);

Textarea.displayName = "Textarea";
