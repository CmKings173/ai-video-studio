"use client";

import React from "react";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  error?: string;
  label?: string;
  options?: { value: string; label: string }[];
}

export const Select = React.forwardRef<HTMLSelectElement, SelectProps>(
  ({ className = "", error, label, id, children, options, ...props }, ref) => {
    return (
      <div className="grid gap-1.5 w-full">
        {label && (
          <label htmlFor={id} className="text-xs font-medium text-[#9ea5b0]">
            {label}
          </label>
        )}
        <select
          id={id}
          ref={ref}
          className={`w-full rounded-md border bg-[#181a1e] px-3 py-2 text-sm text-[#f1f3f5] transition-colors focus:outline-none min-h-[36px] cursor-pointer ${
            error
              ? "border-red-500/80 focus:border-red-500"
              : "border-[#2c3038] focus:border-blue-600"
          } ${className}`}
          {...props}
        >
          {options
            ? options.map((opt) => (
                <option key={opt.value} value={opt.value} className="bg-[#181a1e] text-[#f1f3f5]">
                  {opt.label}
                </option>
              ))
            : children}
        </select>
        {error && <p className="text-xs text-red-400 mt-0.5">{error}</p>}
      </div>
    );
  }
);

Select.displayName = "Select";
