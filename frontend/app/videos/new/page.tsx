"use client";

import { useI18n } from "@/lib/i18n";
import React, { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Sparkles, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { createVideo } from "@/lib/api/videos";
import { listProjects } from "@/lib/api/projects";
import { queryKeys } from "@/lib/query/query-keys";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { PageHeader } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";

const videoFormSchema = (t: (vi: string, en: string) => string) => z
  .object({
    project_id: z.string().min(1, t("Vui lòng chọn dự án", "Please select a project")),
    title: z.string().min(1, t("Tiêu đề không được để trống", "Title is required")).max(255),
    kind: z.enum(["QUICK_CLIP", "LONG_VIDEO"]),
    target_duration: z.coerce.number().min(4).max(60),
    aspect_ratio: z.enum(["9:16", "16:9", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"]),
    brief: z.string().min(1, t("Vui lòng nhập mô tả ý tưởng hoặc prompt", "Please enter a creative brief or prompt")).max(20000),
  })
  .refine(
    (data) => {
      if (data.kind === "QUICK_CLIP") {
        return data.target_duration >= 4 && data.target_duration <= 15;
      }
      if (data.kind === "LONG_VIDEO") {
        return data.target_duration === 30 || data.target_duration === 60;
      }
      return true;
    },
    {
      message: t("Clip ngắn phải từ 4–15 giây; video dài phải là 30 hoặc 60 giây", "Short clips must be 4–15 seconds; long videos must be 30 or 60 seconds"),
      path: ["target_duration"],
    }
  );

type VideoFormValues = z.infer<ReturnType<typeof videoFormSchema>>;

function CreateVideoForm() {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialProjectId = searchParams.get("projectId") || "";
  const queryClient = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);
  const [selectedProjectOption, setSelectedProjectOption] = useState<{ id: string; name: string } | null>(
    initialProjectId ? { id: initialProjectId, name: initialProjectId } : null
  );

  const projectsQuery = useInfiniteQuery({
    queryKey: queryKeys.projects.list({ size: 50, archived: false }),
    queryFn: ({ pageParam }) => listProjects({ page: pageParam, size: 50, archived: false }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const projectItems = flattenPageItems(projectsQuery.data?.pages);

  const projectListComplete = Boolean(
    projectsQuery.data &&
    !projectsQuery.isLoading &&
    !projectsQuery.error &&
    !projectsQuery.isFetchNextPageError &&
    !projectsQuery.hasNextPage
  );
  const soleProject = projectListComplete && projectItems.length === 1 ? projectItems[0] : null;
  const noActiveProjects = projectListComplete && projectItems.length === 0;

  const {
    register,
    control,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<VideoFormValues>({
    resolver: zodResolver(videoFormSchema(t), { errorMap: () => ({ message: t("Giá trị không hợp lệ. Kiểm tra giới hạn của trường này.", "Invalid value. Check the limits for this field.") }) }),
    defaultValues: {
      project_id: initialProjectId || soleProject?.id || "",
      title: "",
      kind: "QUICK_CLIP",
      target_duration: 5,
      aspect_ratio: "9:16",
      brief: "",
    },
  });

  const selectedProjectId = useWatch({ control, name: "project_id" }) || initialProjectId;
  useEffect(() => {
    if (!initialProjectId && soleProject && !selectedProjectId) {
      setValue("project_id", soleProject.id, { shouldValidate: true });
    }
  }, [initialProjectId, selectedProjectId, setValue, soleProject]);

  const visibleProjects: { id: string; name: string }[] = [...projectItems];
  const currentProject = projectItems.find((project) => project.id === selectedProjectId)
    ?? (selectedProjectOption?.id === selectedProjectId ? selectedProjectOption : null);
  if (currentProject && !visibleProjects.some((project) => project.id === currentProject.id)) visibleProjects.unshift(currentProject);

  const selectedKind = useWatch({ control, name: "kind" });

  // Adjust default duration when kind changes
  useEffect(() => {
    if (selectedKind === "QUICK_CLIP") {
      setValue("target_duration", 5);
    } else {
      setValue("target_duration", 30);
    }
  }, [selectedKind, setValue]);

  const createMutation = useMutation({
    mutationFn: (data: VideoFormValues) =>
      createVideo({
        project_id: data.project_id,
        title: data.title,
        kind: data.kind,
        target_duration: data.target_duration,
        aspect_ratio: data.aspect_ratio,
        brief: data.brief,
      }),
    onSuccess: (newVideo) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
      router.push(`/videos/${newVideo.id}`);
    },
    onError: (err) => setServerError(uiError(err)),
  });

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <Link
          href="/videos"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>{t("Quay lại danh sách video", "Back to videos")}</span>
        </Link>

        <PageHeader
          eyebrow={t("Tạo video AI", "AI video creation")}
          title={t("Tạo video mới", "Create video")}
          description={t("Thiết lập mục tiêu, phong cách và kịch bản tổng quan. Hệ thống sẽ chuẩn bị bảng phân cảnh hoặc cảnh đầu tiên.", "Set the goals, style, and outline. The system will prepare a storyboard or the first scene.")}
        />
      </div>

      {serverError && (
        <Alert variant="destructive" title={t("Không thể tạo video", "Unable to create video")}>
          {serverError}
        </Alert>
      )}

      <form
        onSubmit={handleSubmit((data) => createMutation.mutateAsync(data))}
        className="form-card space-y-6"
      >
        <div className="space-y-4">
          <Input
            id="title"
            label={t("Tên video", "Video title")}
            placeholder={t("Ví dụ: Clip mở đầu giới thiệu Aurora 9:16", "Example: Aurora opening clip 9:16")}
            error={errors.title?.message}
            {...register("title")}
          />

          <div className="min-w-0 space-y-2">
            {initialProjectId || soleProject ? (
              <>
                <input type="hidden" {...register("project_id")} />
                <p className="text-xs text-[#9ea5b0]" role="status">
                  {initialProjectId
                    ? t("Video sẽ được lưu trong dự án:", "This video will be stored in project:")
                    : t("Dự án duy nhất đang hoạt động; video sẽ được lưu tại đây:", "This is the only active project; the video will be stored here:")}{" "}
                  <span className="font-medium text-[#f1f3f5]">{currentProject?.name || selectedProjectId}</span>
                </p>
              </>
            ) : noActiveProjects ? (
              <div className="space-y-1">
                <p className="text-xs text-[#9ea5b0]">{t("Chưa có dự án đang hoạt động để lưu video.", "There are no active projects to store this video.")}</p>
                <Link href="/projects" className="text-xs text-blue-400 underline hover:text-blue-300">
                  {t("Tạo dự án", "Create a project")}
                </Link>
              </div>
            ) : (
              <>
                {(() => {
                  const field = register("project_id");
                  return <Select
                    id="project_id"
                    className="h-10 min-w-0 max-w-full"
                    label={t("Dự án", "Project")}
                    error={errors.project_id?.message}
                    aria-describedby="project_scope_help"
                    disabled={!projectItems.length && (projectsQuery.isLoading || Boolean(projectsQuery.error))}
                    {...field}
                    onChange={(event) => {
                      void field.onChange(event);
                      const id = event.target.value;
                      const item = projectItems.find((project) => project.id === id);
                      setSelectedProjectOption(item ?? (id ? { id, name: event.target.selectedOptions[0]?.textContent?.trim() || id } : null));
                    }}
                  >
                    <option value="">{t("-- Chọn dự án --", "-- Select a project --")}</option>
                    {currentProject && !visibleProjects.some((project) => project.id === currentProject.id) && <option value={currentProject.id}>{currentProject.name}</option>}
                    {visibleProjects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
                  </Select>;
                })()}
                <p id="project_scope_help" className="text-xs text-[#9ea5b0]">
                  {t("Chọn dự án nơi lưu video này.", "Choose which project will store this video.")}
                </p>
              </>
            )}
            {projectsQuery.isLoading && <p role="status" className="text-xs text-[#9ea5b0]">{t("Đang tải dự án...", "Loading projects...")}</p>}
            {projectsQuery.error && <div role="alert" className="text-xs text-red-400">{uiError(projectsQuery.error)} <Button type="button" variant="ghost" className="text-xs underline" disabled={projectsQuery.isFetching} onClick={() => projectsQuery.refetch()}>{t("Thử lại", "Retry")}</Button></div>}
            {projectsQuery.hasNextPage && <button type="button" aria-label={t("Tải thêm dự án", "Load more projects")} disabled={projectsQuery.isFetchingNextPage} onClick={() => projectsQuery.fetchNextPage()} className="text-xs text-blue-400 underline disabled:opacity-50">{projectsQuery.isFetchingNextPage ? t("Đang tải dự án...", "Loading projects...") : t("Tải thêm dự án", "Load more projects")}</button>}
            {projectsQuery.isFetchNextPageError && <p role="alert" className="text-xs text-red-400">{t("Không tải được trang dự án tiếp theo. Hãy thử lại.", "Unable to load the next page of projects. Please retry.")} <button type="button" className="underline" disabled={projectsQuery.isFetchingNextPage} onClick={() => projectsQuery.fetchNextPage()}>{t("Thử lại", "Retry")}</button></p>}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Select id="kind" label={t("Định dạng video", "Video format")} {...register("kind")}>
              <option value="QUICK_CLIP">{t("Clip ngắn (1 cảnh, 4–15 giây)", "Short clip (1 scene, 4–15 seconds)")}</option>
              <option value="LONG_VIDEO">{t("Video dài (nhiều cảnh, 30/60 giây)", "Long video (multiple scenes, 30/60 seconds)")}</option>
            </Select>

            <Select
              id="target_duration"
              label={t("Thời lượng (giây)", "Duration (seconds)")}
              error={errors.target_duration?.message}
              {...register("target_duration")}
            >
              {selectedKind === "QUICK_CLIP" ? (
                <>
                  <option value={4}>{t("4 giây", "4 seconds")}</option>
                  <option value={5}>{t("5 giây (khuyến nghị)", "5 seconds (recommended)")}</option>
                  <option value={8}>{t("8 giây", "8 seconds")}</option>
                  <option value={10}>{t("10 giây", "10 seconds")}</option>
                  <option value={15}>{t("15 giây", "15 seconds")}</option>
                </>
              ) : (
                <>
                  <option value={30}>{t("30 giây (bảng phân cảnh)", "30 seconds (storyboard)")}</option>
                  <option value={60}>{t("60 giây (bảng phân cảnh)", "60 seconds (storyboard)")}</option>
                </>
              )}
            </Select>

            <div className="min-w-0 space-y-2">
              <Select id="aspect_ratio" label={t("Tỷ lệ khung hình", "Aspect ratio")} aria-describedby="aspect_ratio_help" {...register("aspect_ratio")}>
                <option value="9:16">{t("9:16 (TikTok / Reels / Shorts)", "9:16 (TikTok / Reels / Shorts)")}</option>
                <option value="16:9">{t("16:9 (ngang TV / YouTube)", "16:9 (landscape TV / YouTube)")}</option>
                <option value="1:1">{t("1:1 (bảng tin vuông)", "1:1 (square feed)")}</option>
                <option value="4:3">4:3</option>
                <option value="3:4">3:4</option>
                <option value="3:2">3:2</option>
                <option value="2:3">2:3</option>
                <option value="21:9">21:9</option>
                <option value="Custom">{t("Tùy chỉnh (chọn khung hình trong cấu hình tạo clip)", "Custom (set the canvas in clip generation settings)")}</option>
              </Select>
              <p id="aspect_ratio_help" className="text-xs leading-relaxed text-[#9ea5b0]">
                {t("Sau khi tạo video, mở cảnh rồi chọn “Cấu hình & tạo clip” để chọn độ phân giải AI và workflow. Độ phân giải MP4 cuối được chọn tại “Ghép & xuất video”.", "After creating the video, open a scene and choose “Configure & generate clip” to select the AI resolution and workflow. Choose the final MP4 resolution in “Assemble & export video”.")}</p>
            </div>
          </div>

          <Textarea
            id="brief"
            label={t("Mô tả ý tưởng / Kịch bản / Prompt chi tiết", "Creative brief / Script / Detailed prompt")}
            placeholder={t("Mô tả bối cảnh, hành động nhân vật, chuyển động máy quay, ánh sáng, góc quay và điểm nhấn sản phẩm...", "Describe the setting, character actions, camera movement, lighting, angles, and product highlights...")}
            error={errors.brief?.message}
            {...register("brief")}
          />
        </div>

        <div className="flex flex-col-reverse items-stretch gap-3 border-t border-[#2c3038] pt-6 sm:flex-row sm:items-center sm:justify-end">
          <Link href="/videos" className="secondary-action w-full shrink-0 whitespace-nowrap text-center text-xs sm:w-auto">
            {t("Hủy", "Cancel")}</Link>
          <Button type="submit" variant="primary" className="w-full min-w-0 sm:w-auto" isLoading={isSubmitting}>
            <Sparkles className="w-4 h-4 shrink-0" />
            <span>{t("Tạo video & mở không gian làm việc", "Create video & open workspace")}</span>
          </Button>
        </div>
      </form>
    </div>
  );
}

export default function CreateVideoPage() {
  const { t } = useI18n();
  return (
    <Suspense fallback={<div className="p-8" role="status" aria-label={t("Đang tải biểu mẫu tạo video", "Loading the create video form")}><div className="animate-pulse h-12 bg-white/5 rounded-md" /></div>}>
      <CreateVideoForm />
    </Suspense>
  );
}
