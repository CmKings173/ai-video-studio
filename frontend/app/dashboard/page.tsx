"use client";

import { useI18n } from "@/lib/i18n";
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
import { PageHeader, MetricCard, StatusPill, EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";

export default function DashboardPage() {
  const { t } = useI18n();
  const { data: summary, isLoading: summaryLoading, isError: summaryError, isFetching: summaryFetching, refetch: refetchSummary } = useQuery({
    queryKey: queryKeys.dashboard.summary,
    queryFn: getDashboardSummary,
  });

  const { data: systemStatus, isLoading: statusLoading, isError: statusError, isFetching: statusFetching, refetch: refetchStatus } = useQuery({
    queryKey: queryKeys.dashboard.systemStatus,
    queryFn: getSystemStatus,
  });

  const { data: videosData, isLoading: videosLoading, isError: videosError, isFetching: videosFetching, refetch: refetchVideos } = useQuery({
    queryKey: queryKeys.videos.list({ size: 5 }),
    queryFn: () => listVideos({ size: 5 }),
  });

  const { data: projectsData, isLoading: projectsLoading, isError: projectsError, isFetching: projectsFetching, refetch: refetchProjects } = useQuery({
    queryKey: queryKeys.projects.list({ size: 4, archived: false }),
    queryFn: () => listProjects({ size: 4, archived: false }),
  });

  const health = statusLoading && !systemStatus
    ? { state: "loading" as const }
    : statusError || !systemStatus
      ? { state: "unavailable" as const }
      : { state: "ready" as const, data: systemStatus };

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("Tổng quan hệ thống", "System overview")}
        title={t("Tổng quan sản xuất video", "Video production overview")}
        description={t("Theo dõi hoạt động tạo video AI, tiến độ tạo cảnh, tài nguyên MinIO và trạng thái cụm ComfyUI / H3.", "Track AI video production, scene creation progress, MinIO assets, and ComfyUI / H3 cluster health.")}
        action={{ href: "/videos/new", label: t("Tạo video mới", "Create video") }}
      />

      {/* Metric Cards Grid */}
      <section className="grid metric-grid" aria-label={t("Số liệu không gian làm việc", "Studio metrics")}>
        {summaryLoading ? (
          <>
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </>
        ) : !summary ? (
          <div className="col-span-full">
            <QueryErrorNotice
              title={t("Không thể tải số liệu tổng quan", "Unable to load overview metrics")}
              detail={t("API chưa trả về số liệu. Thử lại để tải trạng thái mới nhất.", "The API has not returned metrics. Retry to load the latest status.")}
              onRetry={refetchSummary}
              isRetrying={summaryFetching}
            />
          </div>
        ) : (
          <>
            {summaryError && (
              <div className="col-span-full">
                <QueryErrorNotice
                  title={t("Số liệu tổng quan chưa được cập nhật", "Overview metrics could not be refreshed")}
                  detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")}
                  onRetry={refetchSummary}
                  isRetrying={summaryFetching}
                />
              </div>
            )}
            <MetricCard
              label={t("Dự án đang mở", "Active projects")}
              value={summary.projects}
              detail={t("Chiến dịch đang hoạt động", "Active campaigns")}
            />
            <MetricCard
              label={t("Tổng số video", "Total videos")}
              value={summary.videos}
              detail={t(`${summary.assemblies_pending} lượt ghép đang chờ`, `${summary.assemblies_pending} pending assemblies`)}
            />
            <MetricCard
              label={t("Lượt tạo đang chạy", "Creations in progress")}
              value={summary.generations_running}
              detail={t(`${summary.generations_pending} tác vụ trong hàng đợi`, `${summary.generations_pending} jobs in queue`)}
            />
            <MetricCard
              label={t("Tài nguyên sẵn sàng", "Ready assets")}
              value={summary.assets_ready}
              detail={t(`${summary.generations_failed} lượt tạo thất bại`, `${summary.generations_failed} failed creations`)}
            />
          </>
        )}
      </section>

      {/* System Runtime Health Bar */}
      <section className="bg-[#181a1e] border border-[#2c3038] rounded-md p-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Server className="w-5 h-5 text-blue-400" />
            <div>
              <h2 className="text-sm font-semibold text-[#f1f3f5]">{t("Trạng thái cụm dịch vụ nội bộ", "On-premise service cluster health")}</h2>
              <p className="text-xs text-[#9ea5b0]">{t("PostgreSQL · MinIO · ComfyUI · Lưu trữ cục bộ", "PostgreSQL · MinIO · ComfyUI · Local storage")}</p>
            </div>
          </div>

          {health.state === "loading" ? (
            <Skeleton className="w-24 h-6 rounded-full" />
          ) : health.state === "unavailable" ? (
            <span className="text-xs text-amber-300">{t("Trạng thái chưa xác minh", "Status not verified")}</span>
          ) : (
            <Badge status={health.data.status === "ok" ? "OK" : health.data.status === "degraded" ? "DEGRADED" : "UNKNOWN"}>
              {health.data.status === "ok" ? t("Hoạt động tốt", "Healthy") : health.data.status === "degraded" ? t("Có sự cố", "Degraded") : t("Chưa xác minh", "Not verified")}
            </Badge>
          )}
        </div>

        {health.state === "loading" ? (
          <div className="grid responsive-summary-grid gap-3 mt-4 pt-4 border-t border-[#2c3038]/60" aria-label={t("Đang tải trạng thái hệ thống", "Loading system status")}>
            <Skeleton className="h-4" />
            <Skeleton className="h-4" />
            <Skeleton className="h-4" />
            <Skeleton className="h-4" />
          </div>
        ) : health.state === "unavailable" ? (
          <div className="mt-4 pt-4 border-t border-[#2c3038]/60">
            <QueryErrorNotice
              title={t("Không thể lấy trạng thái hệ thống", "Unable to retrieve system status")}
              detail={t("Không thể xác minh tình trạng dịch vụ lúc này.", "Unable to verify service health at this time.")}
              onRetry={refetchStatus}
              isRetrying={statusFetching}
              retryLabel={t("Thử lại trạng thái", "Retry status check")}
            />
          </div>
        ) : (
        <div className="grid responsive-summary-grid gap-3 mt-4 pt-4 border-t border-[#2c3038]/60">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Database className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">Postgres:</span>
            <span className={health.data.postgres.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {health.data.postgres.healthy ? t("Hoạt động tốt", "Healthy") : t("Ngoại tuyến", "Offline")}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <HardDrive className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">{t("Kho MinIO:", "MinIO store:")}</span>
            <span className={health.data.minio.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {health.data.minio.healthy ? t("Sẵn sàng", "Ready") : t("Ngoại tuyến", "Offline")}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Cpu className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">ComfyUI / H3:</span>
            <span className={health.data.comfyui.healthy ? "text-emerald-400 font-semibold" : "text-amber-400 font-semibold"}>
              {health.data.comfyui.healthy ? t("Trực tuyến", "Online") : t("Không thể kết nối", "Unreachable")}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Server className="w-4 h-4 text-[#9ea5b0]" />
            <span className="text-[#9ea5b0]">{t("Ổ đĩa lưu trữ:", "Storage disk:")}</span>
            <span className={health.data.local_storage.healthy ? "text-emerald-400 font-semibold" : "text-red-400 font-semibold"}>
              {health.data.local_storage.healthy ? t("Đủ dung lượng", "Sufficient space") : t("Cảnh báo ổ đĩa", "Disk warning")}
            </span>
          </div>
        </div>
        )}
      </section>

      {/* Main Content Grid: Recent Videos & Active Projects */}
      <section className="grid content-grid">
        <div className="table-panel space-y-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <h2>{t("Video gần đây", "Recent videos")}</h2>
            <Link href="/videos" className="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center gap-1">
              <span>{t("Xem tất cả", "View all")}</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {videosLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
              <Skeleton className="h-12" />
            </div>
          ) : !videosData ? (
            <QueryErrorNotice
              title={t("Không thể tải video gần đây", "Unable to load recent videos")}
              detail={t("Không có dữ liệu video mới để hiển thị.", "No new video data is available to display.")}
              onRetry={refetchVideos}
              isRetrying={videosFetching}
            />
          ) : videosError && !videosData.items.length ? (
            <QueryErrorNotice title={t("Danh sách video chưa được cập nhật", "Video list could not be refreshed")} detail={t("Chưa thể xác minh danh sách video hiện tại. Thử lại để tải dữ liệu mới nhất.", "Unable to verify the current video list. Retry to load the latest data.")} onRetry={refetchVideos} isRetrying={videosFetching} />
          ) : !videosData.items.length ? (
            <EmptyState
              title={t("Chưa có video nào", "No videos yet")}
              detail={t("Bắt đầu tạo video đầu tiên cho chiến dịch của bạn.", "Create the first video for your campaign.")}
              action={{ label: t("Tạo video ngay", "Create video now"), href: "/videos/new" }}
            />
          ) : (
            <>
            {videosError && <QueryErrorNotice title={t("Danh sách video chưa được cập nhật", "Video list could not be refreshed")} detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")} onRetry={refetchVideos} isRetrying={videosFetching} />}
            <div className="table-scroll" role="region" aria-label={t("Video gần đây", "Recent videos")} tabIndex={0}>
              <table>
                <thead>
                  <tr>
                    <th>{t("Tên video", "Video title")}</th>
                    <th>{t("Phân loại", "Category")}</th>
                    <th>{t("Thời lượng", "Duration")}</th>
                    <th>{t("Trạng thái", "Status")}</th>
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
                          {video.kind === "QUICK_CLIP" ? t("Video ngắn", "Quick clip") : t("Video dài", "Long video")} ({video.aspect_ratio})
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
            </div>
            </>
          )}
        </div>

        {/* Active Projects Panel */}
        <div className="table-panel space-y-4">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <h2>{t("Dự án đang triển khai", "Active projects")}</h2>
            <Link href="/projects" className="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center gap-1">
              <span>{t("Tất cả", "All")}</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </Link>
          </div>

          {projectsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          ) : !projectsData ? (
            <QueryErrorNotice
              title={t("Không thể tải dự án gần đây", "Unable to load recent projects")}
              detail={t("Không có dữ liệu dự án mới để hiển thị.", "No new project data is available to display.")}
              onRetry={refetchProjects}
              isRetrying={projectsFetching}
            />
          ) : projectsError && !projectsData.items.length ? (
            <QueryErrorNotice title={t("Danh sách dự án chưa được cập nhật", "Project list could not be refreshed")} detail={t("Chưa thể xác minh danh sách dự án hiện tại. Thử lại để tải dữ liệu mới nhất.", "Unable to verify the current project list. Retry to load the latest data.")} onRetry={refetchProjects} isRetrying={projectsFetching} />
          ) : !projectsData.items.length ? (
            <EmptyState
              title={t("Chưa có dự án", "No projects yet")}
              detail={t("Tạo dự án để gom video và tài nguyên chiến dịch.", "Create a project to group campaign videos and assets.")}
              action={{ label: t("Tạo dự án", "Create project"), href: "/projects" }}
            />
          ) : (
            <>
            {projectsError && <QueryErrorNotice title={t("Danh sách dự án chưa được cập nhật", "Project list could not be refreshed")} detail={t("Đang hiển thị dữ liệu đã tải trước đó.", "Showing previously loaded data.")} onRetry={refetchProjects} isRetrying={projectsFetching} />}
            <div className="space-y-3">
              {projectsData.items.map((project) => (
                <Link
                  key={project.id}
                  href={`/projects/${project.id}`}
                  className="block p-4 rounded-md border border-[#2c3038] bg-[#22252b]/50 hover:bg-[#22252b] hover:border-[#2a2e37] transition-all"
                >
                  <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                    <h3 className="font-semibold text-sm text-[#f1f3f5]">{project.name}</h3>
                    <Badge status={project.archived ? "ARCHIVED" : "READY"} className="text-[10px]" />
                  </div>
                  {project.description && (
                    <p className="text-xs text-[#9ea5b0] mt-1 line-clamp-2">{project.description}</p>
                  )}
                </Link>
              ))}
            </div>
            </>
          )}
        </div>
      </section>
    </div>
  );
}
