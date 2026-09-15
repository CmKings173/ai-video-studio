"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { listVideos } from "@/lib/api/videos";
import { listProjects } from "@/lib/api/projects";
import { listProducts } from "@/lib/api/products";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";

export default function VideosPage() {
  const [search, setSearch] = useState("");
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedKind, setSelectedKind] = useState("");
  const [selectedStatus, setSelectedStatus] = useState("");

  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.all,
    queryFn: () => listProjects({ size: 100, archived: false }),
  });

  const { data: productsData } = useQuery({
    queryKey: queryKeys.products.all,
    queryFn: () => listProducts({ size: 100, archived: false }),
  });

  const { data: videosData, isLoading, error } = useQuery({
    queryKey: queryKeys.videos.list({
      search: search || undefined,
      project_id: selectedProject || undefined,
      kind: selectedKind || undefined,
      status: selectedStatus || undefined,
    }),
    queryFn: () =>
      listVideos({
        search: search || undefined,
        project_id: selectedProject || undefined,
        kind: selectedKind || undefined,
        status: selectedStatus || undefined,
      }),
  });

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Creative Library"
        title="Quản lý video"
        description="Theo dõi toàn bộ quick clip và long video, scene storyboard, generation status và final mp4."
      >
        <Link href="/videos/new" className="primary-action text-xs flex items-center gap-1.5">
          <Plus className="w-4 h-4" />
          <span>Tạo video mới</span>
        </Link>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="relative">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
          <input
            type="text"
            placeholder="Tìm theo tiêu đề..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500 min-h-[38px]"
          />
        </div>

        <select
          value={selectedProject}
          onChange={(e) => setSelectedProject(e.target.value)}
          className="rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500 min-h-[38px]"
        >
          <option value="">Tất cả dự án</option>
          {projectsData?.items.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

        <select
          value={selectedKind}
          onChange={(e) => setSelectedKind(e.target.value)}
          className="rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500 min-h-[38px]"
        >
          <option value="">Tất cả định dạng</option>
          <option value="QUICK_CLIP">Quick Clip (4-15s)</option>
          <option value="LONG_VIDEO">Long Video (30/60s)</option>
        </select>

        <select
          value={selectedStatus}
          onChange={(e) => setSelectedStatus(e.target.value)}
          className="rounded-lg bg-[#0b101a] border border-[#2c3038] text-xs text-[#f1f3f5] px-3 py-2 focus:outline-none focus:border-blue-500 min-h-[38px]"
        >
          <option value="">Tất cả trạng thái</option>
          <option value="DRAFT">DRAFT</option>
          <option value="STORYBOARD_READY">STORYBOARD_READY</option>
          <option value="GENERATING">GENERATING</option>
          <option value="READY">READY</option>
          <option value="DIRTY">DIRTY</option>
          <option value="FAILED">FAILED</option>
        </select>
      </div>

      {/* Videos List */}
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
          <Skeleton className="h-20" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title="Không thể tải video">
          {getErrorMessage(error)}
        </Alert>
      ) : !videosData?.items.length ? (
        <EmptyState
          title="Không tìm thấy video"
          detail={
            search || selectedProject || selectedKind || selectedStatus
              ? "Không có video nào phù hợp với bộ lọc hiện tại."
              : "Bắt đầu tạo video quảng cáo AI đầu tiên từ brief sản phẩm."
          }
          action={{ label: "Tạo video mới ngay", href: "/videos/new" }}
        />
      ) : (
        <section className="list">
          {videosData.items.map((video) => {
            const project = projectsData?.items.find((p) => p.id === video.project_id);
            const product = productsData?.items.find((p) => p.id === video.product_id);

            return (
              <div
                key={video.id}
                className="row hover:border-[#2a2e37] transition-all flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              >
                <div className="space-y-1.5 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link
                      href={`/videos/${video.id}`}
                      className="text-base font-semibold text-[#f1f3f5] hover:text-blue-400 transition-colors"
                    >
                      {video.title}
                    </Link>
                    <span className="text-[11px] font-semibold text-[#9ea5b0] px-2 py-0.5 rounded bg-[#22252b] border border-[#2c3038]">
                      {video.kind === "QUICK_CLIP" ? "Quick Clip" : "Long Video"}
                    </span>
                    <span className="text-[11px] text-[#9ea5b0]">{video.aspect_ratio}</span>
                  </div>

                  <p className="text-xs text-[#9ea5b0]">
                    {project?.name ? `Project: ${project.name}` : ""}
                    {product?.name ? ` · Product: ${product.name}` : ""}
                    {` · Thời lượng: ${video.target_duration}s`}
                  </p>
                </div>

                <div className="flex items-center gap-3 self-end sm:self-center">
                  <StatusPill status={video.status} />
                  <Link
                    href={`/videos/${video.id}`}
                    className="secondary-action text-xs px-3 py-1.5"
                  >
                    Storyboard
                  </Link>
                  <Link
                    href={`/videos/${video.id}/assembly`}
                    className="primary-action text-xs px-3 py-1.5"
                  >
                    Assembly
                  </Link>
                </div>
              </div>
            );
          })}
        </section>
      )}
    </div>
  );
}
