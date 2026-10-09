"use client";

import { useI18n } from "@/lib/i18n";
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
import { PaginationControls } from "@/components/list-pagination";

const projectSchema = z.object({
  name: z.string().min(1, "required_project_name").max(255, "name_too_long"),
  description: z.string().max(20000, "description_too_long").default(""),
});

type ProjectFormValues = z.infer<typeof projectSchema>;

export default function ProjectsPage() {
  const { t, locale } = useI18n();
  const validationMessage = (message?: string) => {
    const messages: Record<string, string> = {
      required_project_name: t("Tên dự án không được để trống", "Project name is required"),
      name_too_long: t("Tên tối đa 255 ký tự", "Name must be at most 255 characters"),
      description_too_long: t("Mô tả tối đa 20.000 ký tự", "Description must be at most 20,000 characters"),
    };
    return message ? messages[message] ?? t("Thông tin không hợp lệ", "Invalid value") : undefined;
  };
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [archived, setArchived] = useState<boolean | undefined>(false);
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  const { data: projectsData, isLoading, isFetching, error, refetch } = useQuery({
    queryKey: queryKeys.projects.list({ page, size: 20, search: search || undefined, archived }),
    queryFn: () => listProjects({ page, size: 20, search: search || undefined, archived }),
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
      setServerError(getErrorMessage(err, t("Không thể tạo dự án", "Unable to create project")));
    },
  });

  const onSubmit = async (data: ProjectFormValues) => {
    setServerError(null);
    await createMutation.mutateAsync(data);
  };

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow={t("Không gian làm việc chiến dịch", "Campaign workspace")}
        title={t("Dự án sản xuất", "Production projects")}
        description={t("Quản lý các chiến dịch quảng cáo, gom nhóm video và tài nguyên theo từng chiến dịch.", "Manage advertising campaigns and group videos and media assets by campaign.")}
      >
        <Button onClick={() => setIsCreateOpen(true)} variant="primary" size="md">
          <Plus className="w-4 h-4" />
          <span>{t("Tạo dự án mới", "Create project")}</span>
        </Button>
      </PageHeader>

      {/* Filter and Search Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 rounded-md border border-[#2c3038] bg-[#181a1e]">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-[#9ea5b0]" />
          <input
            type="text"
            placeholder={t("Tìm kiếm dự án...", "Search projects...")}
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1); }}
            className="w-full pl-10 pr-4 py-2 rounded-lg bg-[#0b101a] border border-[#2c3038] text-sm text-[#f1f3f5] placeholder-[#9ea5b0] focus:outline-none focus:border-blue-500"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <button
            onClick={() => { setArchived(false); setPage(1); }}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === false
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            {t("Đang hoạt động", "Active")}
          </button>
          <button
            onClick={() => { setArchived(true); setPage(1); }}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === true
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            {t("Đã lưu trữ", "Archived")}
          </button>
          <button
            onClick={() => { setArchived(undefined); setPage(1); }}
            className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              archived === undefined
                ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                : "text-[#9ea5b0] hover:text-[#f1f3f5]"
            }`}
          >
            {t("Tất cả", "All")}
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
        <Alert variant="destructive" title={t("Không thể tải danh sách dự án", "Unable to load projects")}>
          {getErrorMessage(error)}
          <button type="button" disabled={isFetching} onClick={() => { if (!isFetching) void refetch(); }} className="ml-3 underline disabled:opacity-50">
            {isFetching ? t("Đang tải...", "Loading...") : t("Thử lại", "Retry")}
          </button>
        </Alert>
      ) : !projectsData?.items.length ? (
        <EmptyState
          title={page > 1 ? t("Trang này không có kết quả", "No results on this page") : t("Không tìm thấy dự án", "Project not found")}
          detail={page > 1 ? t("Quay lại trang trước để xem các kết quả đã tải.", "Go back to the previous page to view loaded results.") : search ? t("Không có dự án nào khớp với từ khóa tìm kiếm.", "No projects match your search.") : t("Bắt đầu tạo dự án đầu tiên của bạn.", "Create your first project to get started.")}
          action={{ label: t("Tạo dự án mới", "Create project"), onClick: () => setIsCreateOpen(true) }}
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
                {project.description || t("Chưa có mô tả dự án.", "No project description yet.")}
              </p>
              <div className="flex items-center justify-between pt-3 mt-auto border-t border-[#2c3038]/60 text-[11px] text-[#9ea5b0]">
                <span className="flex items-center gap-1">
                  <Clock className="w-3.5 h-3.5" />
                  {new Date(project.created_at).toLocaleDateString(locale === "vi" ? "vi-VN" : "en-US")}
                </span>
                <span>{t("Phiên bản #", "Revision #")}{project.revision}</span>
              </div>
            </Card>
          ))}
        </section>
      )}
      <PaginationControls
        data={error ? undefined : projectsData}
        page={page}
        onPageChange={setPage}
        busy={isFetching}
      />

      {/* Create Project Dialog */}
      <Dialog
        isOpen={isCreateOpen}
        onClose={() => {
          setIsCreateOpen(false);
          reset();
          setServerError(null);
        }}
        title={t("Tạo dự án mới", "Create project")}
        description={t("Dự án giúp tổ chức video và liên kết tài nguyên cho cùng một chiến dịch.", "Projects organize videos and link media assets for the same campaign.")}
      >
        {serverError && (
          <Alert variant="destructive" title={t("Lỗi", "Error")}>
            {serverError}
          </Alert>
        )}

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <Input
            id="name"
            label={t("Tên dự án", "Project name")}
            placeholder={t("Ví dụ: Ra mắt mùa hè 2026", "Example: Summer Launch 2026")}
            error={validationMessage(errors.name?.message)}
            {...register("name")}
          />

          <Textarea
            id="description"
            label={t("Mô tả / Yêu cầu sáng tạo của chiến dịch", "Campaign description / creative brief")}
            placeholder={t("Bộ video 9:16 cho sản phẩm nước hoa, tông màu sang trọng...", "9:16 perfume videos with an elegant color palette...")}
            error={validationMessage(errors.description?.message)}
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
              {t("Hủy", "Cancel")}
            </Button>
            <Button type="submit" variant="primary" isLoading={isSubmitting}>
              {t("Tạo dự án", "Create project")}
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
