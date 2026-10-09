"use client";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

type PaginationMetadata = { page: number; page_size: number; total: number };

export function PaginationControls({ data, page, onPageChange, busy = false, label = "Phân trang" }: {
  data?: PaginationMetadata;
  page: number;
  onPageChange: (page: number) => void;
  busy?: boolean;
  label?: string;
}) {
  const { t } = useI18n();
  const current = data?.page === page ? data : undefined;
  const hasNext = current !== undefined && current.page * current.page_size < current.total;
  return (
    <nav aria-label={label === "Phân trang" ? t(label, "Pagination") : label} className="flex flex-wrap items-center justify-between gap-3">
      <span className="text-sm text-[#9ea5b0]" aria-live="polite">
        {current ? t(`Trang ${current.page} / ${Math.max(1, Math.ceil(current.total / current.page_size))} · ${current.total} mục`, `Page ${current.page} / ${Math.max(1, Math.ceil(current.total / current.page_size))} · ${current.total} items`) : t(`Trang ${page}`, `Page ${page}`)}
      </span>
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" disabled={busy || page <= 1}
          onClick={() => { if (!busy && page > 1) onPageChange(page - 1); }}>{t("Trang trước", "Previous")}</Button>
        <Button variant="secondary" size="sm" disabled={busy || !hasNext}
          onClick={() => { if (!busy && hasNext) onPageChange(page + 1); }}>{t("Trang sau", "Next")}</Button>
      </div>
    </nav>
  );
}
