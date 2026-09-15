"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Sparkles, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { createVideo } from "@/lib/api/videos";
import { listProjects } from "@/lib/api/projects";
import { listProducts } from "@/lib/api/products";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { PageHeader } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Alert } from "@/components/ui/alert";

const videoFormSchema = z
  .object({
    project_id: z.string().min(1, "Vui lòng chọn dự án"),
    product_id: z.string().optional().nullable(),
    title: z.string().min(1, "Tiêu đề không được để trống").max(255),
    kind: z.enum(["QUICK_CLIP", "LONG_VIDEO"]),
    target_duration: z.coerce.number().min(4).max(60),
    aspect_ratio: z.enum(["9:16", "16:9", "1:1"]),
    brief: z.string().min(1, "Vui lòng nhập creative brief hoặc prompt").max(20000),
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
      message: "Quick clip phải từ 4-15 giây; Long video phải là 30 hoặc 60 giây",
      path: ["target_duration"],
    }
  );

type VideoFormValues = z.infer<typeof videoFormSchema>;

function CreateVideoForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialProjectId = searchParams.get("projectId") || "";
  const queryClient = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);

  const { data: projectsData } = useQuery({
    queryKey: queryKeys.projects.all,
    queryFn: () => listProjects({ size: 100, archived: false }),
  });

  const { data: productsData } = useQuery({
    queryKey: queryKeys.products.all,
    queryFn: () => listProducts({ size: 100, archived: false }),
  });

  const {
    register,
    control,
    handleSubmit,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<VideoFormValues>({
    resolver: zodResolver(videoFormSchema),
    defaultValues: {
      project_id: initialProjectId,
      product_id: "",
      title: "",
      kind: "QUICK_CLIP",
      target_duration: 5,
      aspect_ratio: "9:16",
      brief: "",
    },
  });

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
        product_id: data.product_id ? data.product_id : null,
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
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  return (
    <div className="max-w-3xl space-y-8">
      <div>
        <Link
          href="/videos"
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Quay lại danh sách video</span>
        </Link>

        <PageHeader
          eyebrow="AI Video Creation"
          title="Tạo video mới"
          description="Thiết lập mục tiêu, phong cách và kịch bản tổng quan. Hệ thống sẽ chuẩn bị storyboard hoặc scene ban đầu."
        />
      </div>

      {serverError && (
        <Alert variant="destructive" title="Không thể tạo video">
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
            label="Tên video"
            placeholder="Ví dụ: Aurora hero opening clip 9:16"
            error={errors.title?.message}
            {...register("title")}
          />

          <div className="field-grid">
            <Select
              id="project_id"
              label="Dự án (Project)"
              error={errors.project_id?.message}
              {...register("project_id")}
            >
              <option value="">-- Chọn dự án --</option>
              {projectsData?.items.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>

            <Select
              id="product_id"
              label="Sản phẩm liên kết (Tùy chọn)"
              error={errors.product_id?.message}
              {...register("product_id")}
            >
              <option value="">-- Không liên kết sản phẩm --</option>
              {productsData?.items.map((prod) => (
                <option key={prod.id} value={prod.id}>
                  {prod.name}
                </option>
              ))}
            </Select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <Select id="kind" label="Định dạng video" {...register("kind")}>
              <option value="QUICK_CLIP">Quick Clip (1 scene, 4-15s)</option>
              <option value="LONG_VIDEO">Long Video (Đa scene, 30/60s)</option>
            </Select>

            <Select
              id="target_duration"
              label="Thời lượng (giây)"
              error={errors.target_duration?.message}
              {...register("target_duration")}
            >
              {selectedKind === "QUICK_CLIP" ? (
                <>
                  <option value={4}>4 giây</option>
                  <option value={5}>5 giây (Khuyến nghị)</option>
                  <option value={8}>8 giây</option>
                  <option value={10}>10 giây</option>
                  <option value={15}>15 giây</option>
                </>
              ) : (
                <>
                  <option value={30}>30 giây (Storyboard)</option>
                  <option value={60}>60 giây (Storyboard)</option>
                </>
              )}
            </Select>

            <Select id="aspect_ratio" label="Tỷ lệ khung hình" {...register("aspect_ratio")}>
              <option value="9:16">9:16 (TikTok / Reels / Shorts)</option>
              <option value="16:9">16:9 (Ngang TV / YouTube)</option>
              <option value="1:1">1:1 (Vuông Feed)</option>
            </Select>
          </div>

          <Textarea
            id="brief"
            label="Creative brief / Kịch bản / Prompt chi tiết"
            placeholder="Mô tả bối cảnh, hành động nhân vật, camera movement, ánh sáng, góc quay và điểm nhấn sản phẩm..."
            error={errors.brief?.message}
            {...register("brief")}
          />
        </div>

        <div className="flex items-center justify-end gap-3 pt-6 border-t border-[#2c3038]">
          <Link href="/videos" className="secondary-action text-xs">
            Hủy
          </Link>
          <Button type="submit" variant="primary" isLoading={isSubmitting}>
            <Sparkles className="w-4 h-4" />
            <span>Tạo video &amp; Mở Workspace</span>
          </Button>
        </div>
      </form>
    </div>
  );
}

export default function CreateVideoPage() {
  return (
    <Suspense fallback={<div className="p-8"><div className="animate-pulse h-12 bg-white/5 rounded-md" /></div>}>
      <CreateVideoForm />
    </Suspense>
  );
}
