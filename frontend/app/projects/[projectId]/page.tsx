"use client";

import { useI18n } from "@/lib/i18n";
import React, { use, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  UploadCloud,
  Edit2,
  Archive,
  ArrowLeft,
} from "lucide-react";
import {
  getProject,
  patchProject,
  archiveProject,
  getProjectVideos,
  getProjectAssets,
} from "@/lib/api/projects";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState, QueryErrorNotice } from "@/components/page-kit";
import { PaginationControls } from "@/components/list-pagination";
import { RelatedResourceState } from "@/components/related-resource-state";
import { useRevisionRecovery } from "@/lib/hooks/use-revision-recovery";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Tabs } from "@/components/ui/tabs";

const editSchema = z.object({
  name: z.string().min(1, "required_project_name").max(255, "name_too_long"),
  description: z.string().max(20000, "description_too_long").default(""),
});

type EditFormValues = z.infer<typeof editSchema>;

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  useI18n();
  const { projectId } = use(params);
  return <ProjectDetailBody key={`detail-${projectId}`} projectId={projectId} />;
}

function ProjectDetailBody({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      required_project_name: t("Tên dự án không được để trống", "Project name is required"),
      name_too_long: t("Tên tối đa 255 ký tự", "Name must be at most 255 characters"),
      description_too_long: t("Mô tả tối đa 20.000 ký tự", "Description must be at most 20,000 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("videos");
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [editRevision, setEditRevision] = useState<number | null>(null);
  const [assetsPage, setAssetsPage] = useState(1);
  const [videosPage, setVideosPage] = useState(1);

  const {
    data: project,
    isLoading: projectLoading,
    error: projectError,
    refetch: refetchProject,
  } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });

  const videosQuery = useQuery({
    queryKey: queryKeys.projects.videos(projectId, { page: videosPage, size: 20 }),
    queryFn: () => getProjectVideos(projectId, { page: videosPage, size: 20 }),
  });
  const videosData = videosQuery.data;

  const assetsQuery = useQuery({
    queryKey: queryKeys.projects.assets(projectId, { page: assetsPage, size: 20 }),
    queryFn: () => getProjectAssets(projectId, { page: assetsPage, size: 20 }),
  });
  const assetsData = assetsQuery.data;

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
  });

  const recovery = useRevisionRecovery({
    refetch: refetchProject,
    dirty: isDirty,
    onRecovered: (current) => { setEditRevision(current.revision); reset({ name: current.name, description: current.description || "" }); },
  });

  const openEditDialog = () => {
    if (isSubmitting || recovery.recovering) return;
    if (recovery.conflict) { setIsEditOpen(true); return; }
    if (project) {
      reset({ name: project.name, description: project.description || "" });
      setEditRevision(project.revision);
      recovery.clear();
      setIsEditOpen(true);
    }
  };

  const editMutation = useMutation({
    mutationFn: (data: EditFormValues) => {
      if (recovery.blocked) throw new Error(t("Vui lòng tải lại dữ liệu trước khi thử lại.", "Please reload the data before retrying."));
      if (!project) throw new Error(t("Chưa tải được dự án", "Project not loaded"));
      return patchProject(projectId, data, editRevision ?? project.revision);
    },
    onSuccess: (saved) => {
      reset({ name: saved.name, description: saved.description || "" });
      setEditRevision(saved.revision);
      recovery.clear();
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      setIsEditOpen(false);
    },
    onError: (err) => {
      recovery.fail(err);
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (recovery.blocked) throw new Error(t("Vui lòng tải lại dữ liệu trước khi thử lại.", "Please reload the data before retrying."));
      if (!project) throw new Error(t("Chưa tải được dự án", "Project not loaded"));
      return archiveProject(projectId, project.revision);
    },
    onSuccess: () => {
      recovery.clear();
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
    onError: (err) => {
      recovery.fail(err);
    },
  });

  if (projectLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-32" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title={t("Không tìm thấy dự án", "Project not found")}>
          {getErrorMessage(projectError, t("Dự án không tồn tại hoặc bạn không có quyền truy cập.", "The project does not exist or you do not have access."))}
        </Alert>
        <Link href="/projects" className="secondary-action inline-flex flex-wrap items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>{t("Quay lại danh sách dự án", "Back to projects")}</span>
        </Link>
      </div>
    );
  }

  const mutationNotice = recovery.message && (
    <Alert variant="destructive" title={t("Thông báo", "Notice")}>
      <div className="space-y-3">
        <p>{recovery.message}</p>
        {recovery.reloadMessage && <p role="alert">{t(`Không tải được dữ liệu mới nhất: ${recovery.reloadMessage}. Bản nháp vẫn được giữ.`, `Unable to load the latest data: ${recovery.reloadMessage}. Your draft is preserved.`)}</p>}
        {recovery.conflict && <Button type="button" size="sm" variant="secondary" disabled={recovery.recovering} onClick={recovery.recover}>
          {recovery.recovering ? t("Đang tải lại...", "Reloading...") : t("Tải lại", "Reload")}
        </Button>}
      </div>
    </Alert>
  );

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/projects"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>{t("Tất cả dự án", "All projects")}</span>
        </Link>

        <PageHeader
          eyebrow={t(`Chi tiết dự án · Phiên bản #${project.revision}`, `Project details · Revision #${project.revision}`)}
          title={project.name}
          description={project.description || t("Chưa có mô tả cho dự án này.", "No description for this project yet.")}
        >
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" disabled={isSubmitting || recovery.recovering} onClick={openEditDialog}>
              <Edit2 className="w-4 h-4" />
              <span>{t("Chỉnh sửa", "Edit")}</span>
            </Button>
            {!project.archived && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (!recovery.blocked && confirm(t("Bạn có chắc chắn muốn lưu trữ dự án này?", "Are you sure you want to archive this project?"))) {
                    archiveMutation.mutate();
                  }
                }}
                disabled={recovery.blocked}
                isLoading={archiveMutation.isPending}
              >
                <Archive className="w-4 h-4" />
                <span>{t("Lưu trữ", "Archive")}</span>
              </Button>
            )}
            <Link href="/assets/upload" className="primary-action text-xs flex items-center gap-1.5">
              <UploadCloud className="w-4 h-4" />
              <span>{t("Tải tài nguyên", "Upload assets")}</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      {!isEditOpen && mutationNotice}
      {projectError && <QueryErrorNotice title={t("Không thể làm mới thông tin dự án", "Unable to refresh project information")}
        detail={t(`Đang hiển thị dữ liệu đã lưu; dữ liệu này có thể đã cũ. ${getErrorMessage(projectError)}`, `Showing saved data, which may be outdated. ${getErrorMessage(projectError)}`)}
        onRetry={recovery.recover} isRetrying={recovery.recovering} />}

      {/* Tabs */}
      <Tabs
        activeTab={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: "videos", label: t("Video trong dự án", "Project videos"), count: videosQuery.error ? undefined : videosData?.total },
          { id: "assets", label: t("Tài nguyên tham chiếu", "Reference assets"), count: assetsQuery.error ? undefined : assetsData?.total },
        ]}
      />

      {/* Tab 1: Videos in Project */}
      {activeTab === "videos" && (
        <div className="space-y-4">
          <RelatedResourceState query={videosQuery} page={videosPage} label={t("video trong dự án", "project videos")} empty={
            <EmptyState
              title={t("Chưa có video nào trong dự án", "No videos in this project yet")}
              detail={t("Tạo video mới để bắt đầu sản xuất cho chiến dịch này.", "Create a video to start production for this campaign.")}
              action={{ label: t("Tạo video mới", "Create video"), href: `/videos/new?projectId=${projectId}` }}
            />
          }>
            <div className="table-panel">
              <div className="table-scroll" role="region" aria-label={t("Video trong dự án", "Project videos")} tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("Tên video", "Video title")}</th>
                      <th>{t("Loại video", "Video type")}</th>
                      <th>{t("Thời lượng", "Duration")}</th>
                      <th>{t("Trạng thái", "Status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {videosData?.items.map((video) => (
                      <tr key={video.id} className="hover:bg-white/5 transition-colors">
                        <td>
                          <Link href={`/videos/${video.id}`} className="font-semibold hover:text-blue-400">
                            {video.title}
                          </Link>
                        </td>
                        <td className="text-xs text-[#9ea5b0]">
                          {video.kind} ({video.aspect_ratio})
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
            </div>
          </RelatedResourceState>
          <PaginationControls data={videosQuery.error ? undefined : videosData} page={videosPage} onPageChange={setVideosPage} busy={videosQuery.isFetching} label={t("Phân trang video trong dự án", "Project video pagination")} />
        </div>
      )}

      {/* Tab 2: Assets in Project */}
      {activeTab === "assets" && (
        <div className="space-y-4">
          <RelatedResourceState query={assetsQuery} page={assetsPage} label={t("tài nguyên dự án", "project assets")} empty={
            <EmptyState
              title={t("Chưa có tài nguyên nào", "No assets yet")}
              detail={t("Tải lên hình ảnh sản phẩm, video tham chiếu hoặc nhạc nền cho dự án.", "Upload product images, reference videos, or background audio for the project.")}
              action={{ label: t("Tải tài nguyên lên", "Upload assets"), href: "/assets/upload" }}
            />
          }>
            <div className="table-panel">
              <div className="table-scroll" role="region" aria-label={t("Tài nguyên tham chiếu", "Reference assets")} tabIndex={0}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("Tệp", "File")}</th>
                      <th>{t("Loại tài nguyên", "Media type")}</th>
                      <th>{t("Vai trò", "Role")}</th>
                      <th>{t("Kích thước", "Size")}</th>
                      <th>{t("Trạng thái", "Status")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {assetsData?.items.map((asset) => (
                      <tr key={asset.id} className="hover:bg-white/5 transition-colors">
                        <td className="font-medium text-sm text-[#f1f3f5]">{asset.filename}</td>
                        <td className="text-xs text-[#9ea5b0]">{asset.content_type}</td>
                        <td className="text-xs font-semibold text-blue-400">{asset.role}</td>
                        <td className="text-xs text-[#9ea5b0]">
                          {(asset.size_bytes / (1024 * 1024)).toFixed(2)} MB
                        </td>
                        <td>
                          <StatusPill status={asset.status} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </RelatedResourceState>
          <PaginationControls data={assetsQuery.error ? undefined : assetsData} page={assetsPage} onPageChange={setAssetsPage} busy={assetsQuery.isFetching} label={t("Phân trang tài nguyên dự án", "Project asset pagination")} />
        </div>
      )}

      {/* Edit Project Dialog */}
      <Dialog
        isOpen={isEditOpen}
        onClose={() => { if (!isSubmitting && !recovery.recovering) setIsEditOpen(false); }}
        title={t("Chỉnh sửa dự án", "Edit project")}
        description={t(`Cập nhật thông tin dự án (Phiên bản #${editRevision ?? project.revision})`, `Update project information (Revision #${editRevision ?? project.revision})`)}
      >
        <form onSubmit={handleSubmit(async (data) => { if (!recovery.blocked) await editMutation.mutateAsync(data).catch(() => {}); })} className="space-y-4">
          {mutationNotice}
          <fieldset disabled={isSubmitting || recovery.recovering} className="space-y-4">
            <Input id="name" label={t("Tên dự án", "Project name")} error={validationMessage(errors.name?.message)} {...register("name")} />
            <Textarea
              id="description"
              label={t("Mô tả / Yêu cầu sáng tạo", "Description / creative brief")}
              error={validationMessage(errors.description?.message)}
              {...register("description")}
            />
            <div className="flex flex-wrap justify-end gap-3 pt-4 border-t border-[#2c3038]">
              <Button type="button" variant="outline" disabled={isSubmitting || recovery.recovering} onClick={() => { if (!isSubmitting && !recovery.recovering) setIsEditOpen(false); }}>
                {t("Hủy", "Cancel")}
              </Button>
              <Button type="submit" variant="primary" disabled={recovery.blocked} isLoading={isSubmitting}>
                {t("Lưu thay đổi", "Save changes")}
              </Button>
            </div>
          </fieldset>
        </form>
      </Dialog>
    </div>
  );
}
