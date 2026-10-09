"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState } from "react";
import Link from "next/link";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { listVideos } from "@/lib/api/videos";
import { listProjects } from "@/lib/api/projects";
import { listProducts } from "@/lib/api/products";
import { queryKeys } from "@/lib/query/query-keys";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { PaginationControls } from "@/components/list-pagination";

export default function VideosPage() {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedProjectOption, setSelectedProjectOption] = useState<{ id: string; name: string } | null>(null);
  const [selectedProduct, setSelectedProduct] = useState("");
  const [selectedProductOption, setSelectedProductOption] = useState<{ id: string; name: string } | null>(null);
  const [selectedKind, setSelectedKind] = useState("");
  const [selectedStatus, setSelectedStatus] = useState("");

  const projectsQuery = useInfiniteQuery({
    queryKey: queryKeys.projects.list({ size: 50, archived: false }),
    queryFn: ({ pageParam }) => listProjects({ page: pageParam, size: 50, archived: false }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const projectItems = flattenPageItems(projectsQuery.data?.pages);

  const productsQuery = useInfiniteQuery({
    queryKey: queryKeys.products.list({ size: 50, archived: false }),
    queryFn: ({ pageParam }) => listProducts({ page: pageParam, size: 50, archived: false }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const productItems = flattenPageItems(productsQuery.data?.pages);

  const visibleProjects: { id: string; name: string }[] = [...projectItems];
  if (selectedProjectOption && !visibleProjects.some((project) => project.id === selectedProject)) {
    visibleProjects.unshift(selectedProjectOption);
  }
  const visibleProducts: { id: string; name: string }[] = [...productItems];
  if (selectedProductOption && !visibleProducts.some((product) => product.id === selectedProduct)) {
    visibleProducts.unshift(selectedProductOption);
  }

  const { data: videosData, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.videos.list({
      page,
      size: 20,
      search: search || undefined,
      project_id: selectedProject || undefined,
      product_id: selectedProduct || undefined,
      kind: selectedKind || undefined,
      status: selectedStatus || undefined,
    }),
    queryFn: () =>
      listVideos({
        page,
        size: 20,
        search: search || undefined,
        project_id: selectedProject || undefined,
        product_id: selectedProduct || undefined,
        kind: selectedKind || undefined,
        status: selectedStatus || undefined,
      }),
  });

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("Thư viện video", "Video library")}
        title={t("Quản lý video", "Manage videos")}
        description={t("Theo dõi clip ngắn, video dài, bảng phân cảnh, tiến độ tạo clip và bản MP4 cuối.", "Track short clips, long videos, storyboards, clip generation progress, and final MP4 exports.")}
      >
        <Link href="/videos/new" className="primary-action text-xs flex items-center gap-1.5">
          <Plus className="w-4 h-4" />
          <span>{t("Tạo video mới", "Create video")}</span>
        </Link>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 items-start gap-3 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="min-w-0 space-y-1.5">
          <label htmlFor="video_title_search" className="text-xs font-medium text-[#9ea5b0]">{t("Tiêu đề", "Title")}</label>
          <div className="relative h-[38px] min-w-0 self-start">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
            <input
              type="text"
              id="video_title_search"
              placeholder={t("Tìm theo tiêu đề...", "Search by title...")}
              value={search}
              onChange={(e) => { setSearch(e.target.value); setPage(1); }}
              className="h-[38px] w-full min-w-0 max-w-full pl-9 pr-3 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <div className="min-w-0 space-y-1.5">
          <label htmlFor="video_project_filter" className="text-xs font-medium text-[#9ea5b0]">{t("Dự án", "Project")}</label>
          <select
            id="video_project_filter"
            aria-label={t("Lọc theo dự án", "Filter by project")}
            value={selectedProject}
            onChange={(event) => {
              const id = event.target.value;
              setSelectedProject(id);
              setPage(1);
              setSelectedProjectOption(projectItems.find((project) => project.id === id) ?? null);
            }}
            className="h-[38px] w-full min-w-0 max-w-full rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500"
          >
            <option value="">{t("Tất cả dự án", "All projects")}</option>
            {visibleProjects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          {projectsQuery.isLoading && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải dự án...", "Loading projects...")}</p>}
          {projectsQuery.error && <div role="alert" className="text-xs text-red-400">{uiError(projectsQuery.error)} <Button type="button" variant="ghost" className="text-xs underline" disabled={projectsQuery.isFetching} onClick={() => projectsQuery.refetch()}>{t("Thử lại", "Retry")}</Button></div>}
          {projectsQuery.hasNextPage && <button type="button" aria-label={t("Tải thêm dự án", "Load more projects")} disabled={projectsQuery.isFetchingNextPage} onClick={() => projectsQuery.fetchNextPage()} className="text-xs text-blue-400 underline disabled:opacity-50">{projectsQuery.isFetchingNextPage ? t("Đang tải dự án...", "Loading projects...") : t("Tải thêm dự án", "Load more projects")}</button>}
          {projectsQuery.isFetchNextPageError && <p role="alert" className="text-xs text-red-400">{t("Không tải được trang dự án tiếp theo. Hãy thử lại.", "Unable to load the next page of projects. Please retry.")}</p>}
        </div>

        <div className="min-w-0 space-y-1.5">
          <label htmlFor="video_product_filter" className="text-xs font-medium text-[#9ea5b0]">{t("Sản phẩm", "Product")}</label>
          <select
            id="video_product_filter"
            aria-label={t("Lọc theo sản phẩm", "Filter by product")}
            value={selectedProduct}
            onChange={(event) => {
              const id = event.target.value;
              setSelectedProduct(id);
              setPage(1);
              setSelectedProductOption(productItems.find((product) => product.id === id) ?? null);
            }}
            className="h-[38px] w-full min-w-0 max-w-full rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500"
          >
            <option value="">{t("Tất cả sản phẩm", "All products")}</option>
            {visibleProducts.map((product) => <option key={product.id} value={product.id}>{product.name}</option>)}
          </select>
          {productsQuery.isLoading && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải sản phẩm...", "Loading products...")}</p>}
          {productsQuery.error && <div role="alert" className="text-xs text-red-400">{uiError(productsQuery.error)} <Button type="button" variant="ghost" className="text-xs underline" disabled={productsQuery.isFetching} onClick={() => productsQuery.refetch()}>{t("Thử lại", "Retry")}</Button></div>}
          {productsQuery.hasNextPage && <button type="button" aria-label={t("Tải thêm sản phẩm", "Load more products")} disabled={productsQuery.isFetchingNextPage} onClick={() => productsQuery.fetchNextPage()} className="text-xs text-blue-400 underline disabled:opacity-50">{productsQuery.isFetchingNextPage ? t("Đang tải sản phẩm...", "Loading products...") : t("Tải thêm sản phẩm", "Load more products")}</button>}
          {productsQuery.isFetchNextPageError && <p role="alert" className="text-xs text-red-400">{t("Không tải được trang sản phẩm tiếp theo. Hãy thử lại.", "Unable to load the next page of products. Please retry.")}</p>}
        </div>

        <div className="min-w-0 space-y-1.5">
          <label htmlFor="video_kind_filter" className="text-xs font-medium text-[#9ea5b0]">{t("Định dạng", "Format")}</label>
          <select
            id="video_kind_filter"
            aria-label={t("Lọc theo định dạng", "Filter by format")}
            value={selectedKind}
            onChange={(e) => { setSelectedKind(e.target.value); setPage(1); }}
            className="h-[38px] w-full min-w-0 max-w-full rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500"
          >
            <option value="">{t("Tất cả định dạng", "All formats")}</option>
            <option value="QUICK_CLIP">{t("Clip ngắn (4–15 giây)", "Short clip (4–15 seconds)")}</option>
            <option value="LONG_VIDEO">{t("Video dài (30/60 giây)", "Long video (30/60 seconds)")}</option>
          </select>
        </div>

        <div className="min-w-0 space-y-1.5">
          <label htmlFor="video_status_filter" className="text-xs font-medium text-[#9ea5b0]">{t("Trạng thái", "Status")}</label>
          <select
            id="video_status_filter"
            aria-label={t("Lọc theo trạng thái", "Filter by status")}
            value={selectedStatus}
            onChange={(e) => { setSelectedStatus(e.target.value); setPage(1); }}
            className="h-[38px] w-full min-w-0 max-w-full rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500"
          >
            <option value="">{t("Tất cả trạng thái", "All statuses")}</option>
            <option value="DRAFT">{t("Bản nháp", "Draft")}</option>
            <option value="STORYBOARD_READY">{t("Bảng phân cảnh sẵn sàng", "Storyboard ready")}</option>
            <option value="GENERATING">{t("Đang tạo", "Generating")}</option>
            <option value="READY">{t("Sẵn sàng", "Ready")}</option>
            <option value="DIRTY">{t("Cần cập nhật", "Needs updating")}</option>
            <option value="FAILED">{t("Thất bại", "Failed")}</option>
          </select>
        </div>
      </div>

      {/* Videos List */}
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title={t("Không thể tải video", "Unable to load videos")}>
          {uiError(error)}
          <Button type="button" variant="ghost" disabled={isFetching} onClick={() => { if (!isFetching) void refetch(); }} className="ml-3 text-xs underline">
            {isFetching ? t("Đang tải...", "Loading...") : t("Thử lại", "Retry")}
          </Button>
        </Alert>
      ) : !videosData?.items.length ? (
        <EmptyState
          title={page > 1 ? t("Trang này không có kết quả", "No results on this page") : t("Không tìm thấy video", "Video not found")}
          detail={
            page > 1 ? t("Quay lại trang trước để xem các video đã tải.", "Return to the previous page to view loaded videos.") :
            search || selectedProject || selectedProduct || selectedKind || selectedStatus
              ? t("Không có video nào phù hợp với bộ lọc hiện tại.", "No videos match the current filters.")
              : t("Tạo video quảng cáo AI đầu tiên từ mô tả sản phẩm.", "Create your first AI advertising video from a product brief.")
          }
          action={{ label: t("Tạo video mới ngay", "Create your first video"), href: "/videos/new" }}
        />
      ) : (
        <section className="list">
          {videosData.items.map((video) => {
            const project = projectItems.find((p) => p.id === video.project_id) ?? (selectedProjectOption?.id === video.project_id ? selectedProjectOption : undefined);
            const product = productItems.find((p) => p.id === video.product_id) ?? (selectedProductOption?.id === video.product_id ? selectedProductOption : undefined);

            return (
              <div
                key={video.id}
                className="row hover:border-[#2a2e37] transition-all flex-wrap flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              >
                <div className="space-y-1.5 min-w-0 max-w-full sm:flex-1 sm:basis-64">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link
                      href={`/videos/${video.id}`}
                      className="min-w-0 max-w-full text-base font-semibold text-[#f1f3f5] hover:text-blue-400 transition-colors"
                    >
                      {video.title}
                    </Link>
                    <span className="text-[11px] font-semibold text-[#9ea5b0] px-2 py-0.5 rounded bg-[#22252b] border border-[#2c3038]">
                      {video.kind === "QUICK_CLIP" ? t("Clip ngắn", "Short clip") : t("Video dài", "Long video")}
                    </span>
                    <span className="text-[11px] text-[#9ea5b0]">{video.aspect_ratio}</span>
                  </div>

                  <p className="text-xs text-[#9ea5b0]">
                    {project?.name ? t(`Dự án: ${project.name}`, `Project: ${project.name}`) : ""}
                    {product?.name ? t(` · Sản phẩm: ${product.name}`, ` · Product: ${product.name}`) : ""}
                    {t(` · Thời lượng: ${video.target_duration} giây`, ` · Duration: ${video.target_duration} seconds`)}
                  </p>
                </div>

                <div className="flex flex-wrap min-w-0 max-w-full shrink-0 items-center justify-end gap-3 self-end sm:self-center">
                  <StatusPill status={video.status} />
                  <Link
                    href={`/videos/${video.id}`}
                    className="secondary-action text-xs px-3 py-1.5"
                  >
                    {t("Bảng phân cảnh", "Storyboard")}</Link>
                  <Link
                    href={`/videos/${video.id}/assembly`}
                    className="primary-action text-xs px-3 py-1.5"
                  >
                    {t("Ghép & xuất video", "Assemble & export video")}</Link>
                </div>
              </div>
            );
          })}
        </section>
      )}
      <PaginationControls
        data={error ? undefined : videosData}
        page={page}
        onPageChange={setPage}
        busy={isFetching}
      />
    </div>
  );
}
