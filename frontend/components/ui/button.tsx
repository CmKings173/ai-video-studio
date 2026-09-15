"use client";

import React from "react";
import { Loader2 } from "lucide-react";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  isLoading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "primary", size = "md", isLoading = false, disabled, children, ...props }, ref) => {
    const baseStyles =
      "inline-flex items-center justify-center font-semibold rounded-md transition-colors duration-150 focus:outline-none disabled:opacity-50 disabled:pointer-events-none gap-2";

    const variantStyles = {
      primary:
        "bg-blue-600 hover:bg-blue-700 text-white border border-transparent",
      secondary:
        "bg-[#22252b] hover:bg-[#2a2e37] text-[#f1f3f5] border border-[#2c3038]",
      outline:
        "bg-transparent hover:bg-[#22252b] text-[#c3c6d7] hover:text-[#f1f3f5] border border-[#2c3038]",
      ghost:
        "bg-transparent hover:bg-[#22252b] text-[#9ea5b0] hover:text-[#f1f3f5] border-transparent",
      danger:
        "bg-red-600/10 hover:bg-red-600/20 text-red-400 border border-red-600/40",
    };

    const sizeStyles = {
      sm: "text-xs px-3 py-1.5 min-h-[32px]",
      md: "text-sm px-3.5 py-2 min-h-[36px]",
      lg: "text-sm px-4 py-2.5 min-h-[40px]",
    };

    return (
      <button
        ref={ref}
        disabled={disabled || isLoading}
        className={`${baseStyles} ${variantStyles[variant]} ${sizeStyles[size]} ${className}`}
        {...props}
      >
        {isLoading && <Loader2 className="w-4 h-4 animate-spin text-current" />}
        {children}
      </button>
    );
  }
);

Button.displayName = "Button";
