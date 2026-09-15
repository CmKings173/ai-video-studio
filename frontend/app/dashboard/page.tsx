"use client";

import React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Server,
  HardDrive,
  Cpu,
  Database,
  ArrowRight,
} from "lucide-react";
import { getDashboardSummary, getSystemStatus } from "@/lib/api/dashboard";
import { listVideos } from "@/lib/api/videos";
import { listProjects } from "@/lib/api/projects";
import { queryKeys } from "@/lib/query/query-keys";
import { PageHeader, MetricCard, StatusPill, EmptyState } from "@/components/page-kit";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

export default function DashboardPage() {
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: queryKeys.dashboard.summary,
    queryFn: getDashboardSummary,
  });

  const { data: systemStatus, isLoading: statusLoading } = useQuery({
    queryKey: queryKeys.dashboard.systemStatus,
    queryFn: getSystemStatus,
  });

  const { data: videosData, isLoading: videosLoading } = useQuery({
    queryKey: queryKeys.videos.list({ size: 5 }),
    queryFn: () => listVideos({ size: 5 }),
  });

  const { data: projectsData, isLoading: projectsLoading } = useQuery({
    queryKey: queryKeys.projects.list({ size: 4, archived: false }),
    queryFn: () => listProjects({ size: 4, archived: false }),
  });

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Tổng quan hệ thống"
        title="Dashboard sản xuất video"
        description="Theo dõi lưu lượng tạo video AI, tiến độ scene generation, tài nguyên MinIO và trạng thái cụm ComfyUI / H3."
        action={{ href: "/videos/new", label: "Tạo video mới" }}
      />

      {/* Metric Cards Grid */}
      <section className="grid metric-grid" aria-label="Studio metrics">
        {summaryLoading ? (
          <>
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </>
        ) : (
          <>
            <MetricCard
              label="Dự án đang mở"
              value={summary?.projects ?? 0}
              detail="Campaigns đang active"
            />
            <MetricCard
              label="Tổng số video"
              value={summary?.videos ?? 0}
              detail={`${summary?.assemblies_pending ?? 0} assembly đang chờ`}
            />
            <MetricCard
              label="Generation đang chạy"
              value={summary?.generations_running ?? 0}
              detail={`${summary?.generations_pending ?? 0} job đang trong queue`}
            />
            <MetricCard
              label="Tài nguyên sẵn sàng"
              value={summary?.assets_ready ?? 0}
              detail={`${summary?.generations_failed ?? 0} generation thất bại`}
            />
          </>
        )}
      </section>

      {/* System Runtime Health Bar */}
      <section className="bg-[#181a1e] border border-[#2c3038] rounded-md p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <Server className="w-5 h-5 text-blue-400" />
            <div>
              <h2 className="text-sm font-semibold text-[#f1f3f5]">Trạng thái cụm dịch vụ On-Premise</h2>
              <p className="text-xs text-[#9ea5b0]">PostgreSQL · MinIO · ComfyUI · Local Storage</p>
            </div>
          </div>

          {statusLoading ? (
            <Skeleton className="w-24 h-6 rounded-full" />
          ) : (
            <Badge status={systemStatus?.status === "ok" ? "OK" : "DEGRADED"}>
              {systemStatus?.status === "ok" ? "Hoạt động tốt" : "Có sự cố"}
            </Badge>
          )}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4 pt-4 border-t border-[#2c3038]/60">
          <div className="flex items-center gap-2 text-xs">
            <Database className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">Postgres:</span>
            <span className={systemStatus?.postgres.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {systemStatus?.postgres.healthy ? "Healthy" : "Offline"}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <HardDrive className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">MinIO Store:</span>
            <span className={systemStatus?.minio.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {systemStatus?.minio.healthy ? "Ready" : "Offline"}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <Cpu className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">ComfyUI / H3:</span>
            <span className={systemStatus?.comfyui.healthy ? "text-emerald-400 font-semibold" : "text-amber-400 font-semibold"}>
              {systemStatus?.comfyui.healthy ? "Online" : "Unreachable"}
            </span>
          </div>

          <div className="flex items-center gap-2 text-xs">
            <Server className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">Storage Disk:</span>
            <span className={systemStatus?.local_storage.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {systemStatus?.local_storage.healthy ? "Đủ dung lượng" : "Cảnh báo disk"}
            </span>
          </div>
        </div>
      </section>

      {/* Main Content Grid: Recent Videos & Active Projects */}
      <section className="grid content-grid">
        <div className="table-panel space-y-4">
          <div className="flex items-center justify-between">
            <h2>Video gần đây</h2>
            <Link href="/videos" className="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center gap-1">
              <span>Xem tất cả</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {videosLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : !videosData?.items.length ? (
            <EmptyState
              title="Chưa có video nào"
              detail="Bắt đầu tạo creative video đầu tiên cho chiến dịch của bạn."
              action={{ label: "Tạo video ngay", href: "/videos/new" }}
            />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Tên video</th>
                  <th>Phân loại</th>
                  <th>Thời lượng</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {videosData.items.map((video) => (
                  <tr key={video.id} className="hover:bg-white/5 transition-colors">
                    <td>
                      <Link href={`/videos/${video.id}`} className="font-semibold hover:text-blue-400">
                        {video.title}
                      </Link>
                    </td>
                    <td>
                      <span className="text-xs text-[#9ea5b0]">
                        {video.kind === "QUICK_CLIP" ? "Quick Clip" : "Long Video"} ({video.aspect_ratio})
                      </span>
                    </td>
                    <td className="text-xs text-[#9ea5b0]">{video.target_duration}s</td>
                    <td>
                      <StatusPill status={video.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Active Projects Panel */}
        <div className="table-panel space-y-4">
          <div className="flex items-center justify-between">
            <h2>Dự án đang triển khai</h2>
            <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center gap-1">
              <span>Tất cả</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {projectsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          ) : !projectsData?.items.length ? (
            <EmptyState
              title="Chưa có dự án"
              detail="Tạo dự án để gom video và tài nguyên chiến dịch."
              action={{ label: "Tạo dự án", href: "/projects" }}
            />
          ) : (
            <div className="space-y-3">
              {projectsData.items.map((project) => (
                <Link
                  key={project.id}
                  href={`/projects/${project.id}`}
                  className="block p-4 rounded-md border border-[#2c3038] bg-[#22252b]/50 hover:bg-[#22252b] hover:border-[#2a2e37] transition-all"
                >
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-semibold text-sm text-[#f1f3f5]">{project.name}</h3>
                    <Badge status={project.archived ? "ARCHIVED" : "READY"} className="text-[10px]" />
                  </div>
                  {project.description && (
                    <p className="text-xs text-[#9ea5b0] mt-1 line-clamp-2">{project.description}</p>
                  )}
                </Link>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
