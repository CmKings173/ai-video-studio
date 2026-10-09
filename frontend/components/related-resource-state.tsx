"use client";

import type { ReactNode } from "react";
import { EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { getErrorMessage } from "@/lib/api/errors";
import { useI18n } from "@/lib/i18n";

export function RelatedResourceState({ query, page, label, empty, children }: {
  query: { data?: { items: unknown[] }; isLoading: boolean; error: unknown; isFetching: boolean; refetch: () => unknown };
  page: number;
  label: string;
  empty: ReactNode;
  children: ReactNode;
}) {
  const { t } = useI18n();
  const populated = Boolean(query.data?.items.length);
  return (
    <>
      {query.error && <QueryErrorNotice
        title={query.data ? t(`Không thể làm mới ${label}`, `Unable to refresh ${label}`) : t(`Không tải được ${label} (trang ${page})`, `Unable to load ${label} (page ${page})`)}
        detail={`${query.data ? (populated ? t("Đang hiển thị dữ liệu đã lưu; dữ liệu này có thể đã cũ. ", "Showing cached data, which may be outdated. ") : t("Không thể xác nhận danh sách hiện tại. ", "Unable to confirm the current list. ")) : ""}${getErrorMessage(query.error)}`}
        onRetry={() => { if (!query.isFetching) return query.refetch(); }}
        isRetrying={query.isFetching}
      />}
      {populated ? children : query.isLoading || query.isFetching ? (
        <p role="status" className="text-sm text-[#9ea5b0]">{t(`Đang tải ${label}...`, `Loading ${label}...`)}</p>
      ) : !query.error && query.data ? (
        page > 1 ? <EmptyState title={t(`Không có mục nào ở trang ${page}`, `No items on page ${page}`)} detail={t("Quay lại trang trước để xem các mục đã tải.", "Return to the previous page to see loaded items.")} /> : empty
      ) : null}
    </>
  );
}
