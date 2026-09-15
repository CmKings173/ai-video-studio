"use client";

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
  RefreshCw,
} from "lucide-react";
import {
  getProject,
  patchProject,
  archiveProject,
  getProjectVideos,
  getProjectAssets,
} from "@/lib/api/projects";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Tabs } from "@/components/ui/tabs";

const editSchema = z.object({
  name: z.string().min(1, "Tên dự án không được để trống").max(255),
  description: z.string().max(20000).default(""),
});

type EditFormValues = z.infer<typeof editSchema>;

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = use(params);
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("videos");
  const [isEditOpen, setIsEditOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    data: project,
    isLoading: projectLoading,
    error: projectError,
    refetch: refetchProject,
  } = useQuery({
    queryKey: queryKeys.projects.detail(projectId),
    queryFn: () => getProject(projectId),
  });

  const { data: videosData, isLoading: videosLoading } = useQuery({
    queryKey: queryKeys.projects.videos(projectId),
    queryFn: () => getProjectVideos(projectId),
  });

  const { data: assetsData, isLoading: assetsLoading } = useQuery({
    queryKey: queryKeys.projects.assets(projectId),
    queryFn: () => getProjectAssets(projectId),
  });

  const {
    register,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<EditFormValues>({
    resolver: zodResolver(editSchema),
  });

  const openEditDialog = () => {
    if (project) {
      setValue("name", project.name);
      setValue("description", project.description || "");
      setServerError(null);
      setIsEditOpen(true);
    }
  };

  const editMutation = useMutation({
    mutationFn: (data: EditFormValues) => {
      if (!project) throw new Error("Project not loaded");
      return patchProject(projectId, data, project.revision);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      setIsEditOpen(false);
    },
    onError: (err) => {
      setServerError(getErrorMessage(err));
    },
  });

  const archiveMutation = useMutation({
    mutationFn: () => {
      if (!project) throw new Error("Project not loaded");
      return archiveProject(projectId, project.revision);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
    },
    onError: (err) => {
      setServerError(getErrorMessage(err));
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

  if (projectError || !project) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title="Không tìm thấy dự án">
          {getErrorMessage(projectError, "Dự án không tồn tại hoặc bạn không có quyền truy cập.")}
        </Alert>
        <Link href="/projects" className="secondary-action inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>Quay lại danh sách dự án</span>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <Link
          href="/projects"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Tất cả dự án</span>
        </Link>

        <PageHeader
          eyebrow={`Chi tiết dự án · Rev #${project.revision}`}
          title={project.name}
          description={project.description || "Chưa có mô tả cho dự án này."}
        >
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={openEditDialog}>
              <Edit2 className="w-4 h-4" />
              <span>Chỉnh sửa</span>
            </Button>
            {!project.archived && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (confirm("Bạn có chắc chắn muốn lưu trữ dự án này?")) {
                    archiveMutation.mutate();
                  }
                }}
                isLoading={archiveMutation.isPending}
              >
                <Archive className="w-4 h-4" />
                <span>Lưu trữ</span>
              </Button>
            )}
            <Link href="/assets/upload" className="primary-action text-xs flex items-center gap-1.5">
              <UploadCloud className="w-4 h-4" />
              <span>Tải tài nguyên</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title="Thông báo">
          <div className="flex items-center justify-between gap-4">
            <span>{serverError}</span>
            {isRevisionConflict(serverError) && (
              <Button size="sm" variant="secondary" onClick={() => refetchProject()}>
                <RefreshCw className="w-3.5 h-3.5" />
                <span>Tải lại</span>
              </Button>
            )}
          </div>
        </Alert>
      )}

      {/* Tabs */}
      <Tabs
        activeTab={activeTab}
        onChange={setActiveTab}
        tabs={[
          { id: "videos", label: "Video trong dự án", count: videosData?.total ?? 0 },
          { id: "assets", label: "Tài nguyên tham chiếu", count: assetsData?.total ?? 0 },
        ]}
      />

      {/* Tab 1: Videos in Project */}
      {activeTab === "videos" && (
        <div className="space-y-4">
          {videosLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : !videosData?.items.length ? (
            <EmptyState
              title="Chưa có video nào trong dự án"
              detail="Tạo video mới để bắt đầu sản xuất cho campaign này."
              action={{ label: "Tạo video mới", href: `/videos/new?projectId=${projectId}` }}
            />
          ) : (
            <div className="table-panel">
              <table>
                <thead>
                  <tr>
                    <th>Tên video</th>
                    <th>Loại video</th>
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
          )}
        </div>
      )}

      {/* Tab 2: Assets in Project */}
      {activeTab === "assets" && (
        <div className="space-y-4">
          {assetsLoading ? (
            <div className="space-y-3">
              <Skeleton className="h-14" />
              <Skeleton className="h-14" />
            </div>
          ) : !assetsData?.items.length ? (
            <EmptyState
              title="Chưa có tài nguyên nào"
              detail="Tải lên hình ảnh sản phẩm, reference video hoặc background audio cho dự án."
              action={{ label: "Tải asset lên", href: "/assets/upload" }}
            />
          ) : (
            <div className="table-panel">
              <table>
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Loại media</th>
                    <th>Vai trò</th>
                    <th>Kích thước</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {assetsData.items.map((asset) => (
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
          )}
        </div>
      )}

      {/* Edit Project Dialog */}
      <Dialog
        isOpen={isEditOpen}
        onClose={() => setIsEditOpen(false)}
        title="Chỉnh sửa dự án"
        description={`Cập nhật thông tin dự án (Revision #${project.revision})`}
      >
        <form onSubmit={handleSubmit((data) => editMutation.mutateAsync(data))} className="space-y-4">
          <Input id="name" label="Tên dự án" error={errors.name?.message} {...register("name")} />
          <Textarea
            id="description"
            label="Mô tả / Creative brief"
            error={errors.description?.message}
            {...register("description")}
          />
          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button type="button" variant="outline" onClick={() => setIsEditOpen(false)}>
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isSubmitting}>
              Lưu thay đổi
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
