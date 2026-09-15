"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Search, Clock } from "lucide-react";
import { listProjects, createProject } from "@/lib/api/projects";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader, Card, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";

const projectSchema = z.object({
  name: z.string().min(1, "Tên dự án không được để trống").max(255),
  description: z.string().max(20000).default(""),
});

type ProjectFormValues = z.infer<typeof projectSchema>;

export default function ProjectsPage() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [archived, setArchived] = useState<boolean | undefined>(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const { data: projectsData, isLoading, error } = useQuery({
    queryKey: queryKeys.projects.list({ search: search || undefined, archived }),
    queryFn: () => listProjects({ search: search || undefined, archived }),
  });

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<ProjectFormValues>({
    resolver: zodResolver(projectSchema),
    defaultValues: { name: "", description: "" },
  });

  const createMutation = useMutation({
    mutationFn: createProject,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      setIsCreateOpen(false);
      reset();
    },
    onError: (err) => {
      setServerError(getErrorMessage(err, "Không thể tạo dự án"));
    },
  });

  const onSubmit = async (data: ProjectFormValues) => {
    setServerError(null);
    await createMutation.mutateAsync(data);
  };

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Campaign Workspace"
        title="Dự án sản xuất"
        description="Quản lý các chiến dịch quảng cáo, gom nhóm video và tài nguyên media theo từng campaign."
      >
        <Button onClick={() => setIsCreateOpen(true)} variant="primary" size="md">
          <Plus className="w-4 h-4" />
          <span>Tạo dự án mới</span>
        </Button>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
          <input
            type="text"
            placeholder="Tìm kiếm dự án..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <button
            onClick={() => setArchived(false)}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === false
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            Đang hoạt động
          </button>
          <button
            onClick={() => setArchived(true)}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === true
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            Đã lưu trữ
          </button>
          <button
            onClick={() => setArchived(undefined)}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === undefined
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            Tất cả
          </button>
        </div>
      </div>

      {/* Projects Grid */}
      {isLoading ? (
        <div className="grid cards-grid">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title="Không thể tải danh sách dự án">
          {getErrorMessage(error)}
        </Alert>
      ) : !projectsData?.items.length ? (
        <EmptyState
          title="Không tìm thấy dự án"
          detail={search ? "Không có dự án nào khớp với từ khóa tìm kiếm." : "Bắt đầu tạo dự án đầu tiên của bạn."}
          action={{ label: "Tạo dự án mới", onClick: () => setIsCreateOpen(true) }}
        />
      ) : (
        <section className="grid cards-grid">
          {projectsData.items.map((project) => (
            <Card
              href={`/projects/${project.id}`}
              key={project.id}
              title={project.name}
              badge={<StatusPill status={project.archived ? "ARCHIVED" : "READY"} />}
            >
              <p className="line-clamp-3 text-xs text-[#9ea5b0] leading-relaxed">
                {project.description || "Chưa có mô tả dự án."}
              </p>
              <div className="flex items-center justify-between pt-3 mt-auto border-t border-[#2c3038]/60 text-[11px] text-[#9ea5b0]">
                <span className="flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5" />
                  {new Date(project.created_at).toLocaleDateString("vi-VN")}
                </span>
                <span>Rev #{project.revision}</span>
              </div>
            </Card>
          ))}
        </section>
      )}

      {/* Create Project Dialog */}
      <Dialog
        isOpen={isCreateOpen}
        onClose={() => {
          setIsCreateOpen(false);
          reset();
          setServerError(null);
        }}
        title="Tạo dự án mới"
        description="Dự án giúp tổ chức video và liên kết tài nguyên media cho cùng một chiến dịch."
      >
        {serverError && (
          <Alert variant="destructive" title="Lỗi">
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <Input
            id="name"
            label="Tên dự án"
            placeholder="Ví dụ: Summer Launch 2026"
            error={errors.name?.message}
            {...register("name")}
          />

          <Textarea
            id="description"
            label="Mô tả / Creative brief của chiến dịch"
            placeholder="Bộ creative 9:16 cho sản phẩm nước hoa, tone màu sang trọng..."
            error={errors.description?.message}
            {...register("description")}
          />

          <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setIsCreateOpen(false);
                reset();
              }}
            >
              Hủy
            </Button>
            <Button type="submit" variant="primary" isLoading={isSubmitting}>
              Tạo dự án
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
