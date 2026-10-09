"use client";

import { useI18n } from "@/lib/i18n";
import React, { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { assemblyFormSchema, type AssemblyFormValues } from "@/lib/assembly/settings";
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
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { useVideoEvents } from "@/lib/hooks/use-video-events";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
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

export default function AssemblyPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
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

  const audioScope = {
    size: 50,
    project_id: video?.project_id,
    ...(video?.product_id ? { product_id: video.product_id } : {}),
    status: "READY",
    kind: "AUDIO",
  };
  const audioAssetsQuery = useInfiniteQuery({
    queryKey: queryKeys.assets.list(audioScope),
    queryFn: ({ pageParam }) => listAssets({ ...audioScope, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
    enabled: !!video?.project_id,
  });
  const audioAssets = flattenPageItems(audioAssetsQuery.data?.pages);

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
    resolver: zodResolver(assemblyFormSchema, { errorMap: () => ({ message: t("Giá trị không hợp lệ. Kiểm tra giới hạn của trường này.", "Invalid value. Check the limits for this field.") }) }),
    defaultValues: {
      delivery_preset: "",
      fit_mode: "FIT_PAD",
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
        delivery_preset: "",
        fit_mode: "FIT_PAD",
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
  const selectedPreset = useWatch({ control, name: "delivery_preset" });

  const scenes = video?.scenes || [];
  const enabledScenes = scenes.filter((s) => s.enabled);
  const unselectedScenes = enabledScenes.filter((s) => s.selected_generation_fresh !== true);
  const isEligibleForAssembly = enabledScenes.length > 0 && unselectedScenes.length === 0;

  const assembleMutation = useMutation({
    mutationFn: (data: AssemblyFormValues) => {
      if (!video) throw new Error(t("Chưa tải được video", "Video not loaded"));
      const payload: AssemblyRequest = {
        delivery_preset: data.delivery_preset || null,
        fit_mode: data.fit_mode,
        transition: data.transition,
        crossfade_seconds: data.crossfade_seconds,
        audio_mode: data.audio_mode,
        background_audio_asset_id: data.background_audio_asset_id ? data.background_audio_asset_id : null,
        background_volume: data.background_volume,
        ...(data.delivery_preset ? {} : { width: data.width, height: data.height }),
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
    onError: (err) => setServerError(uiError(err)),
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
        <Alert variant="destructive" title={t("Không tìm thấy video", "Video not found")}>
          {uiError(videoError)}
        </Alert>
        <Link href="/videos" className="secondary-action inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>{t("Quay lại danh sách video", "Back to videos")}</span>
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
          <span>{t("Quay lại không gian làm việc với bảng phân cảnh", "Back to storyboard workspace")}</span>
        </Link>

        <PageHeader
          eyebrow={t(`Ghép & xuất MP4 · Bản sửa đổi #${video.revision}`, `Assemble & export MP4 · Revision #${video.revision}`)}
          title={t(`Ghép & xuất video: ${video.title}`, `Assemble & export video: ${video.title}`)}
          description={t(`Ghép ${enabledScenes.length} cảnh đã chọn thành một MP4 hoàn chỉnh. Chọn nhạc nền, độ phân giải MP4 cuối và FPS bên dưới.`, `Assemble ${enabledScenes.length} selected scenes into a complete MP4. Choose background audio, final MP4 resolution, and FPS below.`)}
        >
          <Link
            href={`/videos/${videoId}/final-versions`}
            className="secondary-action text-xs flex items-center gap-1.5"
          >
            <Clock className="w-4 h-4" />
            <span>{t("Lịch sử phiên bản", "Version history")}</span>
          </Link>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title={t("Không thể ghép và xuất video", "Unable to assemble and export video")}>
          {serverError}
        </Alert>
      )}

      {/* Live Assembly Progress (if currently running) */}
      {latestFinal && ["QUEUED", "ASSEMBLING"].includes(latestFinal.status) && (
        <div className="p-5 rounded-md bg-blue-600/10 border border-blue-500/40 space-y-3">
          <div className="flex min-w-0 flex-wrap items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-blue-400 animate-ping" />
              <strong className="text-sm text-[#f1f3f5]">
                {t(`Đang ghép phiên bản #${latestFinal.version_no}`, `Assembling version #${latestFinal.version_no}`)}
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
            label={assemblyProgress[latestFinal.id]?.stage || latestFinal.phase || t("Đang kết xuất video...", "Rendering video...")}
          />
        </div>
      )}

      {/* Readiness Warning */}
      {!isEligibleForAssembly && (
        <Alert variant="warning" title={t("Chưa sẵn sàng xuất video", "Not ready to export")}>
          {t(`Còn ${unselectedScenes.length} cảnh thiếu clip hoặc có clip cần tạo lại. Hãy quay lại`, `${unselectedScenes.length} scenes are missing clips or need regeneration. Return to`)}{" "}
          <Link href={`/videos/${videoId}`} className="underline font-semibold text-white">
            {t("Bảng phân cảnh", "Storyboard")}</Link>{" "}
          {t("để tạo hoặc chọn biến thể hoàn tất trước khi ghép video.", "to generate or select completed variants before assembly.")}
        </Alert>
      )}

      <div className="grid content-grid">
        {/* Left: Sequence of Scenes to be Assembled */}
        <div className="space-y-6">
          <div className="table-panel space-y-4">
            <div className="flex min-w-0 flex-wrap items-center justify-between">
              <h2>{t(`Các cảnh tham gia ghép video (${enabledScenes.length})`, `Scenes included in assembly (${enabledScenes.length})`)}</h2>
              <span className="text-xs text-[#9ea5b0]">
                {t(`Tổng thời lượng: ${enabledScenes.reduce((acc, s) => acc + s.duration_seconds, 0)} giây`, `Total duration: ${enabledScenes.reduce((acc, s) => acc + s.duration_seconds, 0)} seconds`)}
              </span>
            </div>

            <div className="space-y-3">
              {scenes.map((scene, idx) => {
                const isReady = scene.selected_generation_fresh === true;

                return (
                  <div
                    key={scene.id}
                    className={`p-4 rounded-md border flex min-w-0 flex-wrap items-center justify-between gap-4 transition-colors ${
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
                          {scene.spec?.title || t(`Cảnh ${idx + 1}`, `Scene ${idx + 1}`)}
                        </h3>
                        <p className="text-xs text-[#9ea5b0] line-clamp-1">{scene.prompt}</p>
                      </div>
                    </div>

                    <div className="flex items-center gap-3 shrink-0">
                      <span className="text-xs text-[#9ea5b0]">{t(`${scene.duration_seconds} giây`, `${scene.duration_seconds} seconds`)}</span>
                      {!scene.enabled ? (
                        <span className="text-xs text-[#9ea5b0]">{t("Đã tắt", "Disabled")}</span>
                      ) : isReady ? (
                        <span className="text-xs font-semibold text-emerald-400 flex items-center gap-1">
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          <span>{t("Sẵn sàng", "Ready")}</span>
                        </span>
                      ) : (
                        <span className="text-xs font-semibold text-amber-400 flex items-center gap-1">
                          <AlertTriangle className="w-3.5 h-3.5" />
                          <span>{scene.selected_generation_id ? t("Clip cần tạo lại", "Clip needs regeneration") : t("Thiếu clip", "Missing clip")}</span>
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
            <h2>{t("Cấu hình xuất video", "Video export settings")}</h2>

            <div className="space-y-4">
              <Select id="transition" label={t("Hiệu ứng chuyển cảnh", "Transition")} {...register("transition")}>
                <option value="CUT">{t("Cắt nối dứt khoát", "Hard cut")}</option>
                <option value="CROSSFADE">{t("Chồng mờ mượt mà", "Smooth crossfade")}</option>
              </Select>

              {selectedTransition === "CROSSFADE" && (
                <Input
                  id="crossfade_seconds"
                  label={t("Thời gian chồng mờ (giây)", "Crossfade duration (seconds)")}
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="1.5"
                  error={errors.crossfade_seconds?.message}
                  {...register("crossfade_seconds")}
                />
              )}

              <Select id="audio_mode" label={t("Âm thanh của cảnh", "Scene audio")} {...register("audio_mode")}>
                <option value="KEEP_SCENE_AUDIO">{t("Giữ âm thanh gốc của từng cảnh", "Keep original scene audio")}</option>
                <option value="MUTE_SCENE_AUDIO">{t("Tắt tiếng cảnh", "Mute scene audio")}</option>
              </Select>

              <Select
                id="bg_audio"
                label={t("Nhạc nền", "Background audio")}
                {...register("background_audio_asset_id")}
              >
                <option value="">{t("-- Không dùng nhạc nền --", "-- No background audio --")}</option>
                {audioAssets
                  .filter((a) => a.content_type.startsWith("audio/"))
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.filename}
                    </option>
                  ))}
              </Select>
              {audioAssetsQuery.hasNextPage && <Button type="button" variant="secondary" size="sm"
                disabled={audioAssetsQuery.isFetchingNextPage} onClick={() => { void audioAssetsQuery.fetchNextPage(); }}>
                {audioAssetsQuery.isFetchingNextPage ? t("Đang tải âm thanh…", "Loading audio…") : t("Tải thêm tệp âm thanh", "Load more audio files")}
              </Button>}

              <Input
                id="bg_volume"
                label={t("Âm lượng nhạc nền (0.0–1.0)", "Background audio volume (0.0–1.0)")}
                type="number"
                step="0.05"
                min="0"
                max="1"
                error={errors.background_volume?.message}
                {...register("background_volume")}
              />

              <Select id="delivery_preset" label={t("Độ phân giải MP4 cuối", "Final MP4 resolution")} {...register("delivery_preset")}>
                <option value="">{t("Tùy chỉnh", "Custom")}</option>
                <option value="SOCIAL_VERTICAL_1080">{t("Dọc · 1080 × 1920", "Portrait · 1080 × 1920")}</option>
                <option value="LANDSCAPE_FHD">{t("Ngang · 1920 × 1080", "Landscape · 1920 × 1080")}</option>
                <option value="SQUARE_1080">{t("Vuông · 1080 × 1080", "Square · 1080 × 1080")}</option>
                <option value="PORTRAIT_4_5">4:5 · 1080 × 1350</option>
                <option value="PORTRAIT_3_4">3:4 · 1080 × 1440</option>
                <option value="LANDSCAPE_4_3">4:3 · 1440 × 1080</option>
                <option value="ULTRAWIDE_2560_1080">{t("Siêu rộng · 2560 × 1080", "Ultrawide · 2560 × 1080")}</option>
              </Select>
              <Select id="fit_mode" label={t("Cách khớp khung hình", "Frame fitting")} {...register("fit_mode")}>
                <option value="FIT_PAD">{t("Giữ toàn bộ hình, thêm viền", "Keep the full image and add padding")}</option>
                <option value="CENTER_CROP">{t("Lấp đầy khung, cắt giữa", "Fill the frame with a center crop")}</option>
              </Select>
              {!selectedPreset && <div className="grid responsive-field-grid gap-3">
                <Input
                  id="res_w"
                  label={t("Chiều rộng (px)", "Width (px)")}
                  type="number"
                  error={errors.width?.message}
                  {...register("width")}
                />
                <Input
                  id="res_h"
                  label={t("Chiều cao (px)", "Height (px)")}
                  type="number"
                  error={errors.height?.message}
                  {...register("height")}
                />
              </div>}

              <Select id="fps" label={t("Tốc độ khung hình (FPS)", "Frame rate (FPS)")} {...register("fps", { valueAsNumber: true })}>
                <option value={24}>{t("24 FPS (điện ảnh)", "24 FPS (cinematic)")}</option>
                <option value={25}>{t("25 FPS (chuẩn PAL)", "25 FPS (PAL standard)")}</option>
                <option value={30}>{t("30 FPS (video kỹ thuật số)", "30 FPS (digital video)")}</option>
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
              <span>{t("Ghép & xuất MP4", "Assemble & export MP4")}</span>
            </Button>
          </form>
        </aside>
      </div>
    </div>
  );
}
