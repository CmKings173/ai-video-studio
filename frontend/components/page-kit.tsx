"use client";

import React from "react";
import Link from "next/link";
import { Badge } from "./ui/badge";
import { Inbox } from "lucide-react";
import { useI18n } from "@/lib/i18n";

export function QueryErrorNotice({
  title,
  detail,
  onRetry,
  retryLabel = "Thử lại",
  isRetrying = false,
}: {
  title: string;
  detail: string;
  onRetry: () => unknown;
  retryLabel?: string;
  isRetrying?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-red-500/40 bg-red-500/5 p-4">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-[#f1f3f5]">{title}</p>
        <p className="mt-1 text-xs text-[#9ea5b0]">{detail}</p>
      </div>
      <button type="button" className="primary-action text-xs" disabled={isRetrying} onClick={() => { void onRetry(); }}>
        {isRetrying ? t("Đang thử lại…", "Retrying…") : retryLabel === "Thử lại" ? t(retryLabel, "Retry") : retryLabel}
      </button>
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
  children,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: { href: string; label: string; onClick?: () => void };
  children?: React.ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="flex items-center gap-3">
        {action && (
          <Link
            className="primary-action"
            href={action.href}
            onClick={action.onClick}
          >
            {action.label}
          </Link>
        )}
        {children}
      </div>
    </header>
  );
}

export function Card({
  title,
  children,
  href,
  badge,
  className = "",
}: {
  title: string;
  children: React.ReactNode;
  href?: string;
  badge?: React.ReactNode;
  className?: string;
}) {
  const body = (
    <article className={`card ${className}`}>
      <div className="flex items-center justify-between gap-2">
        <h2>{title}</h2>
        {badge}
      </div>
      {children}
    </article>
  );

  return href ? (
    <Link className="card-link group" href={href}>
      {body}
    </Link>
  ) : (
    body
  );
}

export function StatusPill({ status }: { status: string }) {
  return <Badge status={status} />;
}

export function MetricCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string | number;
  detail?: string;
}) {
  return (
    <div className="metric-card">
      <span className="text-xs font-medium">{label}</span>
      <strong>{value}</strong>
      {detail && <small className="text-xs opacity-75">{detail}</small>}
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
  icon,
}: {
  title: string;
  detail: string;
  action?: { label: string; onClick?: () => void; href?: string };
  icon?: React.ReactNode;
}) {
  return (
    <div className="empty-state flex flex-col items-center justify-center space-y-4 border border-[#2c3038] bg-[#181a1e] p-12 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-md border border-[#2c3038] bg-[#22252b] text-[#9ea5b0]">
        {icon || <Inbox className="w-6 h-6" />}
      </div>
      <div className="space-y-1 max-w-sm">
        <h3 className="text-base font-semibold text-[#f1f3f5]">{title}</h3>
        <p className="text-xs leading-relaxed text-[#9ea5b0]">{detail}</p>
      </div>
      {action && (
        <div className="pt-2">
          {action.href ? (
            <Link className="primary-action text-xs" href={action.href}>
              {action.label}
            </Link>
          ) : (
            <button className="primary-action text-xs" onClick={action.onClick}>
              {action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
