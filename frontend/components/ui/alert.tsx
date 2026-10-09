"use client";

import React from "react";
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "info" | "warning" | "destructive" | "success";
  title?: string;
}

export function Alert({ variant = "info", title, children, className = "", ...props }: AlertProps) {
  const variantStyles = {
    info: "border-blue-500/40 bg-blue-500/10 text-blue-300",
    warning: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    destructive: "border-red-500/40 bg-red-500/10 text-red-300",
    success: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
  };

  const icons = {
    info: <Info className="w-5 h-5 text-blue-400 shrink-0" />,
    warning: <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />,
    destructive: <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />,
    success: <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />,
  };

  return (
    <div
      role="alert"
      className={`flex items-start gap-3 rounded-md border p-4 ${variantStyles[variant]} ${className}`}
      {...props}
    >
      {icons[variant]}
      <div className="grid gap-1 text-sm">
        {title && <h5 className="font-semibold leading-none tracking-tight">{title}</h5>}
        <div className="text-xs opacity-90 leading-relaxed">{children}</div>
      </div>
    </div>
  );
}
