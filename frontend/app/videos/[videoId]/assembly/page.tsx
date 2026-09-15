"use client";

import React, { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  Film,
  ArrowLeft,
  CheckCircle2,
  AlertTriangle,
  Clock,
} from "lucide-react";
import { getVideo } from "@/lib/api/videos";
import { assembleVideo, listFinalVersions } from "@/lib/api/assembly";
import { listAssets } from "@/lib/api/assets";
import { queryKeys } from "@/lib/query/query-keys";
import { useVideoEvents } from "@/lib/hooks/use-video-events";
import { getErrorMessage } from "@/lib/api/errors";
import { getProgressPercent } from "@/lib/utils/progress";
import { useIdempotentAction } from "@/lib/hooks/use-idempotent-action";
import { PageHeader } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import type { AssemblyRequest } from "@/lib/api/types";

const assemblyFormSchema = z.object({
  transition: z.enum(["CUT", "CROSSFADE"]),
  crossfade_seconds: z.coerce.number().min(0.1).max(1.5),
  audio_mode: z.enum(["KEEP_SCENE_AUDIO", "MUTE_SCENE_AUDIO"]),
  background_audio_asset_id: z.string().optional().nullable(),
  background_volume: z.coerce.number().min(0).max(1),
  width: z.coerce.number().min(256).max(1920),
  height: z.coerce.number().min(256).max(1920),
  fps: z.coerce.number().refine(
    (val): val is 24 | 25 | 30 => val === 24 || val === 25 || val === 30,
    { message: "FPS must be 24, 25, or 30" }
  ),
});

type AssemblyFormValues = z.infer<typeof assemblyFormSchema>;

export default function AssemblyPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { videoId } = use(params);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);
  const assembleAction = useIdempotentAction<AssemblyRequest & { videoRevision: number }>();

  const { assemblyProgress } = useVideoEvents(videoId);

  const {
    data: video,
    isLoading: videoLoading,
    error: videoError,
  } = useQuery({
    queryKey: queryKeys.videos.detail(videoId),
    queryFn: () => getVideo(videoId),
  });

  const { data: finalVersions } = useQuery({
    queryKey: queryKeys.videos.finalVersions(videoId),
    queryFn: () => listFinalVersions(videoId),
  });

  const { data: audioAssets } = useQuery({
    queryKey: queryKeys.assets.list({ size: 50 }),
    queryFn: () => listAssets({ size: 50 }),
  });

  // Calculate resolution defaults from aspect ratio
  const isLandscape = video?.aspect_ratio === "16:9";
  const isSquare = video?.aspect_ratio === "1:1";
  const defaultWidth = isLandscape ? 1920 : isSquare ? 1080 : 1080;
  const defaultHeight = isLandscape ? 1080 : isSquare ? 1080 : 1920;

  const {
    register,
    control,
    handleSubmit,
    reset,
    formState: { errors, isDirty },
  } = useForm<AssemblyFormValues>({
    resolver: zodResolver(assemblyFormSchema),
    defaultValues: {
      transition: "CUT",
      crossfade_seconds: 0.5,
      audio_mode: "KEEP_SCENE_AUDIO",
      background_audio_asset_id: "",
      background_volume: 0.3,
      width: defaultWidth,
      height: defaultHeight,
      fps: 24,
    },
  });

  // Reset defaults accurately once async video query completes without overriding user edits
  useEffect(() => {
    if (video && !isDirty) {
      const landscape = video.aspect_ratio === "16:9";
      const square = video.aspect_ratio === "1:1";
      reset({
        transition: "CUT",
        crossfade_seconds: 0.5,
        audio_mode: "KEEP_SCENE_AUDIO",
        background_audio_asset_id: "",
        background_volume: 0.3,
        width: landscape ? 1920 : square ? 1080 : 1080,
        height: landscape ? 1080 : square ? 1080 : 1920,
        fps: 24,
      });
    }
  }, [video, reset, isDirty]);

  const selectedTransition = useWatch({ control, name: "transition" });

  const scenes = video?.scenes || [];
  const enabledScenes = scenes.filter((s) => s.enabled);
  const unselectedScenes = enabledScenes.filter((s) => !s.selected_generation_id);
  const isEligibleForAssembly = enabledScenes.length > 0 && unselectedScenes.length === 0;

  const assembleMutation = useMutation({
    mutationFn: (data: AssemblyFormValues) => {
      if (!video) throw new Error("Video not loaded");
      const payload: AssemblyRequest = {
        transition: data.transition,
        crossfade_seconds: data.crossfade_seconds,
        audio_mode: data.audio_mode,
        background_audio_asset_id: data.background_audio_asset_id ? data.background_audio_asset_id : null,
        background_volume: data.background_volume,
        width: data.width,
        height: data.height,
        fps: data.fps,
      };
      const key = assembleAction.getKey({
        videoRevision: video.revision,
        ...payload,
      });
      return assembleVideo(
        videoId,
        payload,
        video.revision,
        key
      );
    },
    onSuccess: () => {
      assembleAction.reset();
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.finalVersions(videoId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      router.push(`/videos/${videoId}/final-versions`);
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const latestFinal = finalVersions?.[0];

  if (videoLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32" />
        <div className="grid content-grid gap-6">
          <Skeleton className="h-96" />
          <Skeleton className="h-96" />
        </div>
      </div>
    );
  }

  if (videoError || !video) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title="Không tìm thấy video">
          {getErrorMessage(videoError)}
        </Alert>
        <Link href="/videos" className="secondary-action inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>Quay lại danh sách video</span>
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <Link
          href={`/videos/${videoId}`}
          className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5 mb-4"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Quay lại Storyboard Workspace</span>
        </Link>

        <PageHeader
          eyebrow={`Assembly & Xuất bản Final MP4 · Rev #${video.revision}`}
          title={`Lắp ghép: ${video.title}`}
          description={`Ghép nối tuần tự ${enabledScenes.length} phân cảnh đã chọn thành một file MP4 hoàn chỉnh với FFmpeg assembler.`}
        >
          <Link
            href={`/videos/${videoId}/final-versions`}
            className="secondary-action text-xs flex items-center gap-1.5"
          >
            <Clock className="w-4 h-4" />
            <span>Lịch sử các phiên bản</span>
          </Link>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title="Lỗi xuất bản Assembly">
          {serverError}
        </Alert>
      )}

      {/* Live Assembly Progress (if currently running) */}
      {latestFinal && ["QUEUED", "ASSEMBLING"].includes(latestFinal.status) && (
        <div className="p-5 rounded-md bg-blue-600/10 border border-blue-500/40 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-400 animate-ping" />
              <strong className="text-sm text-[#f1f3f5]">
                Đang Assemble Final Version #{latestFinal.version_no}
              </strong>
            </div>
            <Badge status={latestFinal.status} />
          </div>
          <Progress
            value={getProgressPercent(
              assemblyProgress[latestFinal.id]?.progress,
              latestFinal.progress_current,
              latestFinal.progress_total
            )}
            label={assemblyProgress[latestFinal.id]?.stage || latestFinal.phase || "Đang render video..."}
          />
        </div>
      )}

      {/* Readiness Warning */}
      {!isEligibleForAssembly && (
        <Alert variant="warning" title="Chưa sẵn sàng xuất bản">
          Còn {unselectedScenes.length} phân cảnh chưa chọn video output. Hãy quay lại{" "}
          <Link href={`/videos/${videoId}`} className="underline font-semibold text-white">
            Storyboard
          </Link>{" "}
          để generate hoặc chọn variant hoàn tất trước khi assemble.
        </Alert>
      )}

      <div className="grid content-grid">
        {/* Left: Sequence of Scenes to be Assembled */}
        <div className="space-y-6">
          <div className="table-panel space-y-4">
            <div className="flex items-center justify-between">
              <h2>Các phân cảnh tham gia lắp ghép ({enabledScenes.length})</h2>
              <span className="text-xs text-[#9ea5b0]">
                Tổng thời lượng: {enabledScenes.reduce((acc, s) => acc + s.duration_seconds, 0)}s
              </span>
            </div>

            <div className="space-y-3">
              {scenes.map((scene, idx) => {
                const isReady = !!scene.selected_generation_id;

                return (
                  <div
                    key={scene.id}
                    className={`p-4 rounded-md border flex items-center justify-between gap-4 transition-colors ${
                      !scene.enabled
                        ? "border-[#2c3038]/40 bg-[#181a1e]/40 opacity-40"
                        : isReady
                        ? "border-emerald-500/30 bg-emerald-500/5"
                        : "border-amber-500/30 bg-amber-500/5"
                    }`}
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-lg bg-[#0b101a] border border-[#2c3038] flex items-center justify-center font-semibold text-xs text-[#9ea5b0] shrink-0">
                        #{idx + 1}
                      </div>
                      <div className="min-w-0">
                        <h3 className="font-semibold text-sm text-[#f1f3f5] truncate">
                          {scene.spec?.title || `Scene ${idx + 1}`}
                        </h3>
                        <p className="text-xs text-[#9ea5b0] line-clamp-1">{scene.prompt}</p>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs text-[#9ea5b0]">{scene.duration_seconds}s</span>
                      {!scene.enabled ? (
                        <span className="text-xs text-[#9ea5b0]">Đã tắt</span>
                      ) : isReady ? (
                        <span className="text-xs font-semibold text-emerald-400 flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>Ready</span>
                        </span>
                      ) : (
                        <span className="text-xs font-semibold text-amber-400 flex items-center gap-1">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          <span>Thiếu clip</span>
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right: Assembly Form */}
        <aside>
          <form
            onSubmit={handleSubmit((data) => assembleMutation.mutateAsync(data))}
            className="form-card space-y-5"
          >
            <h2>Cấu hình xuất bản FFmpeg</h2>

            <div className="space-y-4">
              <Select id="transition" label="Hiệu ứng chuyển cảnh (Transition)" {...register("transition")}>
                <option value="CUT">CUT (Cắt nối dứt khoát)</option>
                <option value="CROSSFADE">CROSSFADE (Chồng mờ mượt mà)</option>
              </Select>

              {selectedTransition === "CROSSFADE" && (
                <Input
                  id="crossfade_seconds"
                  label="Thời gian Crossfade (giây)"
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="1.5"
                  error={errors.crossfade_seconds?.message}
                  {...register("crossfade_seconds")}
                />
              )}

              <Select id="audio_mode" label="Chế độ âm thanh Scene" {...register("audio_mode")}>
                <option value="KEEP_SCENE_AUDIO">Giữ âm thanh gốc của từng scene</option>
                <option value="MUTE_SCENE_AUDIO">Tắt tiếng scene (Mute)</option>
              </Select>

              <Select
                id="bg_audio"
                label="Nhạc nền (Background Audio Asset)"
                {...register("background_audio_asset_id")}
              >
                <option value="">-- Không dùng nhạc nền --</option>
                {audioAssets?.items
                  .filter((a) => a.content_type.startsWith("audio/"))
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.filename}
                    </option>
                  ))}
              </Select>

              <Input
                id="bg_volume"
                label="Âm lượng nhạc nền (0.0 - 1.0)"
                type="number"
                step="0.05"
                min="0"
                max="1"
                error={errors.background_volume?.message}
                {...register("background_volume")}
              />

              <div className="grid grid-cols-2 gap-3">
                <Input
                  id="res_w"
                  label="Chiều rộng (px)"
                  type="number"
                  error={errors.width?.message}
                  {...register("width")}
                />
                <Input
                  id="res_h"
                  label="Chiều cao (px)"
                  type="number"
                  error={errors.height?.message}
                  {...register("height")}
                />
              </div>

              <Select id="fps" label="Tốc độ khung hình (FPS)" {...register("fps", { valueAsNumber: true })}>
                <option value={24}>24 FPS (Cinematic)</option>
                <option value={25}>25 FPS (PAL Standard)</option>
                <option value={30}>30 FPS (Digital Video)</option>
              </Select>
            </div>

            <Button
              type="submit"
              variant="primary"
              disabled={!isEligibleForAssembly}
              isLoading={assembleMutation.isPending}
              className="w-full mt-4"
            >
              <Film className="w-4 h-4" />
              <span>Bắt đầu Assemble Final MP4</span>
            </Button>
          </form>
        </aside>
      </div>
    </div>
  );
}
