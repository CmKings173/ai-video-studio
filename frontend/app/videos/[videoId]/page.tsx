"use client";

import { useI18n } from "@/lib/i18n";
import React, { use, useState, useRef, useMemo, useEffect } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  Sparkles,
  ArrowLeft,
  ArrowUp,
  ArrowDown,
  Play,
  CheckCircle2,
  Eye,
  Film,
  Plus,
  Edit2,
  Check,
  RefreshCw,
  XCircle,
  LayoutGrid,
} from "lucide-react";
import { getVideo, previewStoryboard, publishStoryboard } from "@/lib/api/videos";
import {
  createScene,
  patchScene,
  reorderScenes,
  selectGeneration,
  enableScene,
  disableScene,
} from "@/lib/api/scenes";
import {
  previewPrompt,
  createGeneration,
  createVariation,
  generateAll,
  listSceneGenerations,
  getGeneration,
  cancelGeneration,
  regenerate,
} from "@/lib/api/generations";
import { generateIdempotencyKey } from "@/lib/api/client";
import { queryKeys } from "@/lib/query/query-keys";
import { useVideoEvents } from "@/lib/hooks/use-video-events";
import { ApiClientError, getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { getProgressPercent } from "@/lib/utils/progress";
import { useIdempotentAction } from "@/lib/hooks/use-idempotent-action";
import { useGenerateAllAction } from "@/lib/hooks/use-generate-all-action";
import { PageHeader } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { VideoPreview } from "@/components/ui/video-preview";
import { GenerationEditor } from "@/components/generation/generation-editor";
import { GenerationHistoryDetails } from "@/components/generation/history-details";
import { reuseGenerationSettings } from "@/lib/generation/history-settings";
import { directGenerationRequiresAggregate, historyGenerationRequiresAggregate, eligibleScenes } from "@/lib/generation/eligible-scenes";
import { selectionState, loadSelectedGeneration, executionGroups, editSceneSpec } from "@/lib/generation/workspace-state";
import { BatchSummary } from "@/components/generation/batch-summary";
import type {
  GenerationDTO,
  SceneDTO,
  StoryboardPreviewDTO,
  GenerationRequest,
  VariationRequest,
} from "@/lib/api/types";

const GENERATION_ACTIVE_STATUSES = new Set([
  "CREATED",
  "DISPATCHING",
  "QUEUED",
  "RUNNING",
  "COLLECTING",
  "CANCEL_REQUESTED",
]);

const GENERATION_TERMINAL_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

function isActiveGenerationStatus(status: string): boolean {
  return GENERATION_ACTIVE_STATUSES.has(status);
}

function isTerminalGenerationStatus(status: string): boolean {
  return GENERATION_TERMINAL_STATUSES.has(status);
}


export default function VideoWorkspacePage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { t } = useI18n();
  const statusLabel = (status: string) => ({
    DRAFT: t("Bản nháp", "Draft"), STORYBOARD_READY: t("Bảng phân cảnh sẵn sàng", "Storyboard ready"),
    GENERATING: t("Đang tạo", "Generating"), READY: t("Sẵn sàng", "Ready"), DIRTY: t("Cần cập nhật", "Needs updating"),
    CREATED: t("Đã tạo", "Created"), DISPATCHING: t("Đang gửi", "Dispatching"), QUEUED: t("Đang chờ", "Queued"),
    RUNNING: t("Đang chạy", "Running"), COLLECTING: t("Đang thu thập kết quả", "Collecting outputs"),
    CANCEL_REQUESTED: t("Đã yêu cầu hủy", "Cancellation requested"), COMPLETED: t("Hoàn tất", "Completed"),
    FAILED: t("Thất bại", "Failed"), CANCELLED: t("Đã hủy", "Cancelled"),
  } as Record<string, string>)[status] ?? status;
  const purposeLabel = (purpose: string | undefined) => ({
    HOOK: t("Mở đầu cuốn hút", "Attention-grabbing opening"),
    PRODUCT_DETAIL: t("Cận cảnh sản phẩm", "Product close-up"), BENEFIT: t("Lợi ích sản phẩm", "Product benefits"),
    LIFESTYLE: t("Đời sống & trải nghiệm", "Lifestyle & experience"), CTA: t("Kêu gọi hành động", "Call to action"),
  } as Record<string, string>)[purpose ?? "PRODUCT_DETAIL"] ?? purpose ?? t("Không xác định", "Unknown");
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const { videoId } = use(params);
  const queryClient = useQueryClient();

  // Active Scene & Modals
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null);
  const [isGenerateOpen, setIsGenerateOpen] = useState(false);
  const [isEditSceneOpen, setIsEditSceneOpen] = useState(false);
  const [isPreviewPromptOpen, setIsPreviewPromptOpen] = useState(false);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isAddSceneOpen, setIsAddSceneOpen] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // Idempotency trackers
  const genAction = useIdempotentAction<{ sceneId: string; payload: GenerationRequest }>();
  const generateAllAction = useGenerateAllAction();
  const variationAction = useIdempotentAction<VariationRequest>();
  const regenerateAction = useIdempotentAction<{ sceneId: string; parentId: string }>();

  // SSE real-time hook
  const { generationProgress, sceneActiveGeneration } = useVideoEvents(videoId);

  // Fetch Video Detail
  const {
    data: video,
    isLoading: videoLoading,
    error: videoError,
    refetch: refetchVideo,
  } = useQuery({
    queryKey: queryKeys.videos.detail(videoId),
    queryFn: () => getVideo(videoId),
  });

  const scenes = video?.scenes || [];
  const activeScene = scenes.find((s) => s.id === selectedSceneId) || scenes[0] || null;
  const directRequiresAggregate = !!activeScene && directGenerationRequiresAggregate(activeScene, scenes);

  // Fetch generations for the selected scene
  const [historyPosition, setHistoryPosition] = useState({ sceneId: "", page: 1 });
  const historyPage = historyPosition.sceneId === activeScene?.id ? historyPosition.page : 1;
  const eligibleCount = eligibleScenes(scenes).length;
  const groups = executionGroups(scenes);
  const { data: generationsData, isLoading: genLoading, error: historyError } = useQuery({
    queryKey: [...queryKeys.scenes.generations(activeScene?.id || ""), { page: historyPage, size: 20 }],
    queryFn: () => (activeScene ? listSceneGenerations(activeScene.id, { page: historyPage, size: 20 }) : null),
    enabled: !!activeScene,
  });
  const { data: latestGenerationsData } = useQuery({
    queryKey: [...queryKeys.scenes.generations(activeScene?.id || ""), { page: 1, size: 20 }],
    queryFn: () => activeScene ? listSceneGenerations(activeScene.id, { page: 1, size: 20 }) : null,
    enabled: !!activeScene && historyPage !== 1,
  });
  const activityGenerations = historyPage === 1 ? generationsData : latestGenerationsData;

  const selectedId = activeScene?.selected_generation_id;
  const pagedSelection = generationsData?.items.find((item) => item.id === selectedId);
  const { data: historicalSelection, error: selectedError } = useQuery({
    queryKey: queryKeys.generations.detail(selectedId ?? ""),
    queryFn: () => loadSelectedGeneration(selectedId!, generationsData?.items ?? [], getGeneration),
    enabled: !!selectedId && !genLoading && !pagedSelection,
  });
  const selectedGen = pagedSelection ?? historicalSelection;
  const selectedState = activeScene ? selectionState(activeScene) : "NO_SELECTION";

  // Prompt Preview Query
  const {
    data: promptPreviewData,
    isLoading: promptPreviewLoading,
    refetch: fetchPromptPreview,
  } = useQuery({
    queryKey: [...queryKeys.scenes.promptPreview(activeScene?.id || ""), activeScene?.revision, video?.revision],
    queryFn: () => (activeScene ? previewPrompt(activeScene.id) : null),
    enabled: isPreviewPromptOpen && !!activeScene,
  });

  // Determine active generation and its SSE progress for activeScene
  const activeSceneGeneration = useMemo(() => {
    if (!activeScene) return null;

    // 1. Check SSE sceneActiveGeneration mapping
    const sseGenId = sceneActiveGeneration[activeScene.id];
    if (sseGenId) {
      const live = generationProgress[sseGenId];
      if (live && !isTerminalGenerationStatus(live.status)) {
        const matchingGen = activityGenerations?.items.find((g) => g.id === sseGenId);
        const percent = getProgressPercent(live.progress, matchingGen?.progress_current, matchingGen?.progress_total);
        return { id: sseGenId, progress: { ...live, progress: percent } };
      }
    }

    // 2. Check running items in generationsData
    const running = activityGenerations?.items.find((g) => isActiveGenerationStatus(g.status));
    if (running) {
      const live = generationProgress[running.id];
      const livePercent = live?.progress;
      const percent = getProgressPercent(livePercent, running.progress_current, running.progress_total);
      return {
        id: running.id,
        progress: {
          status: live?.status || running.status,
          stage: live?.stage || running.phase || "",
          progress: percent,
        },
      };
    }

    return null;
  }, [activeScene, sceneActiveGeneration, generationProgress, activityGenerations?.items]);

  // Mutations
  const reorderMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (sceneIds: string[]) => {
      if (!video) throw new Error(t("Chưa tải được video", "Video not loaded"));
      return reorderScenes(videoId, { scene_ids: sceneIds }, video.revision);
    },
    onSuccess: () => {
      setServerError(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const enableDisableMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: ({ sceneId, enable, revision }: { sceneId: string; enable: boolean; revision: number }) =>
      enable ? enableScene(sceneId, revision) : disableScene(sceneId, revision),
    onSuccess: () => {
      setServerError(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const selectGenMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: ({ sceneId, generationId, revision }: { sceneId: string; generationId: string; revision: number }) =>
      selectGeneration(sceneId, { generation_id: generationId }, revision),
    onSuccess: () => {
      setServerError(null);
      generateAllAction.retire();
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      setIsHistoryOpen(false);
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const cancelGenMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (genId: string) => cancelGeneration(genId),
    onSuccess: () => {
      setServerError(null);
      if (activeScene) {
        queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(activeScene.id) });
      }
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const createGenMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (input: { sceneId: string; payload: GenerationRequest }) => {
      const scene = scenes.find((item) => item.id === input.sceneId);
      if ((scene && directGenerationRequiresAggregate(scene, scenes, input.payload)) || input.payload.motion_context?.enabled) {
        throw new Error(t("Cảnh này cần chạy cùng chuỗi cảnh nối tiếp hoặc ngữ cảnh chuyển động. Hãy dùng Tạo toàn bộ.", "This scene must run with its continuous sequence or motion context. Use Generate all."));
      }
      const key = genAction.getKey(input);
      return createGeneration(input.sceneId, input.payload, key);
    },
    onSuccess: (_result, input) => {
      setServerError(null);
      genAction.reset();
      queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(input.sceneId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      setIsGenerateOpen(false);
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const variationMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (parentGeneration: GenerationDTO) => {
      const scene = scenes.find((item) => item.id === parentGeneration.scene_id);
      if (scene && historyGenerationRequiresAggregate(scene, scenes, parentGeneration)) throw new Error(t("Cảnh này cần chạy cùng chuỗi cảnh nối tiếp hoặc ngữ cảnh chuyển động; hãy dùng Tạo toàn bộ.", "This scene must run with its continuous sequence or motion context; use Generate all."));
      const payload: VariationRequest = { parent_generation_id: parentGeneration.id };
      const key = variationAction.getKey(payload);
      return createVariation(parentGeneration.scene_id, payload, key);
    },
    onSuccess: (_result, parent) => {
      setServerError(null);
      variationAction.reset();
      queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(parent.scene_id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const reuseSettingsMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (parent: GenerationDTO) => {
      const scene = scenes.find((item) => item.id === parent.scene_id);
      if (!scene) throw new Error(t("Chưa tải được cảnh", "Scene not loaded"));
      return patchScene(scene.id, { generation_config: reuseGenerationSettings(parent) }, scene.revision);
    },
    onSuccess: () => {
      setServerError(null);
      generateAllAction.retire();
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      setIsHistoryOpen(false);
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const regenerateMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (parent: GenerationDTO) => {
      const input = { sceneId: parent.scene_id, parentId: parent.id };
      const scene = scenes.find((item) => item.id === parent.scene_id);
      if (scene && historyGenerationRequiresAggregate(scene, scenes, parent)) throw new Error(t("Cảnh này cần chạy cùng chuỗi cảnh nối tiếp hoặc ngữ cảnh chuyển động; hãy dùng Tạo toàn bộ.", "This scene must run with its continuous sequence or motion context; use Generate all."));
      return regenerate(input.sceneId, input.parentId, regenerateAction.getKey(input));
    },
    onSuccess: (_result, parent) => {
      setServerError(null);
      regenerateAction.reset();
      queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(parent.scene_id) });
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(uiError(err)),
  });

  const generateAllMutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: async () => {
      if (!video) throw new Error(t("Chưa tải được video", "Video not loaded"));
      const { payload, key } = await generateAllAction.prepare(video, async () => {
        const refreshed = await refetchVideo();
        if (refreshed.error) throw refreshed.error;
        if (!refreshed.data) throw new Error(t("Chưa tải được video", "Video not loaded"));
        return refreshed.data;
      });
      return generateAll(videoId, payload, key);
    },
    onSuccess: () => {
      setServerError(null);
      generateAllAction.reset();
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => {
      if (
        err instanceof ApiClientError &&
        (err.status === 409 || err.status === 412) &&
        (err.code === "IDEMPOTENCY_KEY_REUSED" || err.code === "REVISION_CONFLICT")
      ) {
        generateAllAction.retire();
      }
      setServerError(uiError(err));
    },
  });

  // Reorder handlers
  const handleMove = (index: number, direction: "up" | "down") => {
    if (!video) return;
    const newScenes = [...scenes];
    const targetIdx = direction === "up" ? index - 1 : index + 1;
    if (targetIdx < 0 || targetIdx >= newScenes.length) return;

    const [moved] = newScenes.splice(index, 1);
    newScenes.splice(targetIdx, 0, moved);

    reorderMutation.mutate(newScenes.map((s) => s.id));
  };

  if (videoLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-48" />
        <Skeleton className="h-32 w-full" />
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <Skeleton className="h-96 xl:col-span-2" />
          <Skeleton className="h-96" />
        </div>
      </div>
    );
  }

  if (videoError || !video) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title={t("Không tìm thấy video", "Video not found")}>
          {uiError(videoError, t("Video không tồn tại hoặc đã bị xóa.", "The video does not exist or has been deleted."))}
        </Alert>
        <Link href="/videos" className="secondary-action inline-flex min-w-0 flex-wrap items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>{t("Quay lại danh sách video", "Back to videos")}</span>
        </Link>
      </div>
    );
  }


  return (
    <div className="space-y-8">
      {/* Top Header */}
      <div>
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-4 mb-3">
          <Link
            href="/videos"
            className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>{t("Tất cả video", "All videos")}</span>
          </Link>

        </div>

        <PageHeader
          eyebrow={t(`Không gian làm việc · Bảng phân cảnh · Bản sửa đổi #${video.revision}`, `Workspace · Storyboard · Revision #${video.revision}`)}
          title={video.title}
          description={t(`${video.kind === "QUICK_CLIP" ? "Clip ngắn" : "Video dài"} · Tỷ lệ ${video.aspect_ratio} · Tổng ${video.target_duration} giây · Trạng thái: ${statusLabel(video.status)}`, `${video.kind === "QUICK_CLIP" ? "Short clip" : "Long video"} · Aspect ratio ${video.aspect_ratio} · Total ${video.target_duration} seconds · Status: ${statusLabel(video.status)}`)}
        >
          <div className="flex min-w-0 flex-wrap items-center gap-2 flex-wrap">
            {scenes.length > 0 && (
              <Button
                variant={eligibleCount > 0 ? "primary" : "secondary"}
                size="sm"
                onClick={() => generateAllMutation.mutate()}
                disabled={eligibleCount === 0}
                isLoading={generateAllMutation.isPending}
              >
                <Sparkles className="w-4 h-4 text-blue-400" />
                <span>{eligibleCount > 0 ? t(`Tạo ${eligibleCount} cảnh`, `Generate ${eligibleCount} scenes`) : t("Tất cả cảnh đã cập nhật.", "All scenes are up to date.")}</span>
              </Button>
            )}

            <Link
              href={`/videos/${video.id}/assembly`}
              className={`${eligibleCount === 0 && scenes.some((scene) => scene.enabled) ? "primary-action" : "secondary-action"} text-xs flex items-center gap-1.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400`}
            >
              <Film className="w-4 h-4" />
              <span>{t("Ghép & xuất video", "Assemble & export video")}</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      <BatchSummary video={video} />

      {serverError && (
        <Alert variant="destructive" title={t("Thông báo hệ thống", "System message")}>
          <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
            <span>{serverError}</span>
            <Button size="sm" variant="secondary" onClick={() => refetchVideo()}>
              <RefreshCw className="w-3.5 h-3.5" />
              <span>{t("Làm mới", "Refresh")}</span>
            </Button>
          </div>
        </Alert>
      )}

      {/* When LONG_VIDEO has no scenes: render Storyboard Setup */}
      {video.kind === "LONG_VIDEO" && scenes.length === 0 ? (
        <StoryboardSetupPanel
          video={video}
          onSuccess={() => {
            setServerError(null);
            queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          }}
          onOpenAddScene={() => setIsAddSceneOpen(true)}
          onConflict={() => {
            queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          }}
        />
      ) : (
        /* Main Storyboard Grid */
        <section className="grid content-grid">
          {/* Left: Active Scene Preview & Generation Area */}
          <div className="space-y-6">
            <div className="preview-panel space-y-4">
              <div className="flex min-w-0 flex-wrap items-center justify-between">
                <div>
                  <span className="text-[11px] font-semibold text-blue-400 uppercase tracking-wider">
                    {t(`Cảnh #${activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}`, `Scene #${activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}`)}
                  </span>
                  <h2 className="text-lg font-semibold text-[#f1f3f5] mt-0.5">
                    {activeScene?.spec?.title || t(`Cảnh ${(activeScene?.scene_order ?? 0) + 1}`, `Scene ${(activeScene?.scene_order ?? 0) + 1}`)}
                  </h2>
                </div>

                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setIsPreviewPromptOpen(true);
                      fetchPromptPreview();
                    }}
                  >
                    <Eye className="w-3.5 h-3.5" />
                    <span>{t("Prompt AI", "AI prompt")}</span>
                  </Button>

                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => { setServerError(null); setIsEditSceneOpen(true); }}
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                    <span>{t("Sửa cảnh", "Edit scene")}</span>
                  </Button>

                  <Button
                    size="sm"
                    variant="primary"
                    disabled={!activeScene?.enabled || directRequiresAggregate}
                    onClick={() => {
                      if (!activeScene?.enabled || directRequiresAggregate) return;
                      genAction.reset();
                      setIsGenerateOpen(true);
                    }}
                  >
                    <Play className="w-3.5 h-3.5" />
                    <span>{t("Cấu hình & tạo clip", "Configure & generate clip")}</span>
                  </Button>
                  {directRequiresAggregate && <Button size="sm" variant="secondary" onClick={() => setIsGenerateOpen(true)}>{t("Cấu hình tạo clip", "Configure clip generation")}</Button>}
                </div>
              </div>

              {/* Video / Generation Preview Display */}
              {directRequiresAggregate && activeScene && <Alert>{t("Cảnh này cần chạy cùng nhóm Director để giữ cảnh nối tiếp hoặc ngữ cảnh chuyển động. Lưu cấu hình rồi dùng Tạo toàn bộ.", "This scene must run with its Director group to preserve continuity or motion context. Save the settings, then use Generate all.")}</Alert>}
              {selectedState === "STALE_SELECTION" && <Alert>{t("Clip đã chọn không còn khớp với cảnh, sản phẩm, thương hiệu hoặc cấu hình hiện tại. Cần tạo lại trước khi ghép và xuất video.", "The selected clip no longer matches the current scene, product, brand, or configuration. Generate it again before assembling and exporting.")}</Alert>}
              <div className="preview-frame relative flex flex-col items-center justify-center p-6 text-center min-h-[320px]">
                {activeScene && activeSceneGeneration?.progress ? (
                  <div className="w-full max-w-md space-y-3 p-4 rounded-md bg-[#181a1e]/90 border border-[#2c3038]">
                    <div className="flex min-w-0 flex-wrap items-center justify-between text-xs">
                      <span className="font-semibold text-[#f1f3f5]">
                        {activeSceneGeneration.progress.stage || t("Đang xử lý", "Processing")}
                      </span>
                      <Badge status={activeSceneGeneration.progress.status} />
                    </div>
                    <Progress value={activeSceneGeneration.progress.progress || 0} />
                  </div>
                ) : selectedGen ? (
                  <div className="w-full max-w-lg space-y-3">
                    {selectedGen.status === "COMPLETED" && selectedGen.output_asset_id ? (
                      <VideoPreview assetId={selectedGen.output_asset_id} className="max-h-[360px]" />
                    ) : (
                      <div className="w-16 h-16 rounded-md bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400 mx-auto">
                        <Film className="w-8 h-8" />
                      </div>
                    )}
                    <div>
                      <span className={`text-xs font-semibold uppercase tracking-wider block ${selectedState === "FRESH_SELECTION" ? "text-emerald-400" : "text-amber-300"}`}>
                        {t(`${selectedState === "FRESH_SELECTION" ? "✓" : "Lịch sử ·"} Đã chọn clip #${selectedGen.generation_no}`, `${selectedState === "FRESH_SELECTION" ? "✓" : "History ·"} Selected clip #${selectedGen.generation_no}`)}
                      </span>
                      <p className="text-xs text-[#9ea5b0] mt-1 max-w-sm mx-auto">
                        {t(`Chế độ: ${selectedGen.mode} · Trạng thái: ${statusLabel(selectedGen.status)}`, `Mode: ${selectedGen.mode} · Status: ${statusLabel(selectedGen.status)}`)}
                      </p>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => setIsHistoryOpen(true)}>
                      {t(`Xem tất cả biến thể (${generationsData?.total ?? 0})`, `View all variants (${generationsData?.total ?? 0})`)}
                    </Button>
                  </div>
                ) : selectedId ? (
                  <p role="status">{selectedError ? uiError(selectedError, t("Không tải được clip đã chọn.", "Unable to load the selected clip.")) : t("Đang tải clip đã chọn…", "Loading the selected clip…")}</p>
                ) : (
                  <div className="space-y-3 max-w-sm">
                    <div className="w-12 h-12 rounded-md bg-[#22252b] border border-[#2c3038] flex items-center justify-center text-[#9ea5b0] mx-auto">
                      <Film className="w-6 h-6" />
                    </div>
                    <p className="text-xs text-[#9ea5b0]">
                      {t("Chưa chọn clip cho cảnh này. Mở “Cấu hình & tạo clip” để chọn độ phân giải AI, workflow và tạo clip bằng H3.", "No clip is selected for this scene. Open “Configure & generate clip” to choose the AI resolution and workflow, then generate a clip with H3.")}</p>
                    {generationsData?.items.length ? (
                      <Button size="sm" variant="secondary" onClick={() => setIsHistoryOpen(true)}>
                        {t(`Chọn từ ${generationsData.total} biến thể đã tạo`, `Choose from ${generationsData.total} generated variants`)}
                      </Button>
                    ) : null}
                  </div>
                )}
              </div>

              {/* Scene Prompt & Spec Summary */}
              <div className="p-4 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-2">
                <div className="flex min-w-0 flex-wrap items-center justify-between text-xs">
                  <span className="text-[#9ea5b0] font-semibold uppercase tracking-wider">
                    {t("Prompt của cảnh:", "Scene prompt:")}</span>
                  <span className="text-[#9ea5b0]">
                    {t(`Thời lượng: ${activeScene?.duration_seconds ?? 0} giây · Bản sửa đổi #${activeScene?.revision ?? 0}`, `Duration: ${activeScene?.duration_seconds ?? 0} seconds · Revision #${activeScene?.revision ?? 0}`)}
                  </span>
                </div>
                <p className="text-xs text-[#f1f3f5] leading-relaxed">
                  {activeScene?.prompt || t("Chưa có prompt.", "No prompt yet.")}
                </p>
                {activeScene?.negative_prompt && (
                  <p className="text-[11px] text-red-400/80">
                    {t("Prompt loại trừ:", "Negative prompt:")}{activeScene.negative_prompt}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Right: Scene Timeline & Ordering Sidebar */}
          <aside className="timeline-panel space-y-4">
            <div className="flex min-w-0 flex-wrap items-center justify-between">
              <h2>{t(`Bảng phân cảnh (${scenes.length})`, `Storyboard (${scenes.length})`)}</h2>
              {video.kind === "LONG_VIDEO" && scenes.length < 15 && (
                <Button size="sm" variant="outline" onClick={() => setIsAddSceneOpen(true)}>
                  <Plus className="w-3.5 h-3.5" />
                  <span>{t("Thêm cảnh", "Add scene")}</span>
                </Button>
              )}
            </div>

            <div className="space-y-3">
              {scenes.map((scene, idx) => {
                const isSelected = activeScene?.id === scene.id;
                const hasSelectedOutput = selectionState(scene) === "FRESH_SELECTION";
                const group = groups.find((group) => group.members.some((member) => member.id === scene.id));

                return (
                  <div
                    key={scene.id}
                    className={`p-3.5 rounded-md border transition-all space-y-2 ${
                      isSelected
                        ? "border-blue-500 bg-blue-500/10 "
                        : "border-[#2c3038] bg-[#181a1e] hover:border-[#2a2e37]"
                    } ${!scene.enabled ? "opacity-50" : ""}`}
                  >
                    <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className="text-xs font-semibold text-[#9ea5b0]">#{idx + 1}</span>
                        <button type="button" onClick={() => setSelectedSceneId(scene.id)} aria-current={isSelected ? "true" : undefined} className="font-semibold text-xs text-[#f1f3f5] truncate rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
                          {scene.spec?.title || t(`Cảnh ${idx + 1}`, `Scene ${idx + 1}`)}
                        </button>
                        {!scene.enabled && (
                          <span className="text-[10px] text-red-400 font-semibold">{t("(Tắt)", "(Disabled)")}</span>
                        )}
                      </div>

                      <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                        {video.kind === "LONG_VIDEO" && (
                          <>
                            <button
                              disabled={idx === 0}
                              onClick={() => handleMove(idx, "up")}
                              className="p-1 rounded text-[#9ea5b0] hover:text-white disabled:opacity-20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
                              title={t("Di chuyển lên", "Move up")}
                            >
                              <ArrowUp className="w-3.5 h-3.5" />
                            </button>
                            <button
                              disabled={idx === scenes.length - 1}
                              onClick={() => handleMove(idx, "down")}
                              className="p-1 rounded text-[#9ea5b0] hover:text-white disabled:opacity-20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
                              title={t("Di chuyển xuống", "Move down")}
                            >
                              <ArrowDown className="w-3.5 h-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                    <p className="text-[11px] text-blue-300">
                      {scene.spec?.continuity === "CONTINUOUS" ? t("↳ Nối tiếp cảnh trước", "↳ Continues from the previous scene") : t("Cảnh độc lập", "Independent scene")}
                      {group && (group.members.length > 1 || group.members.some((member) => member.generation_config?.motion_context?.enabled)) && t(` · Cảnh ${group.members.map((member) => member.scene_order + 1).join("–")} · cùng nhóm Director`, ` · Scenes ${group.members.map((member) => member.scene_order + 1).join("–")} · same Director group`)}
                    </p>
                    {selectionState(scene) === "STALE_SELECTION" && <p className="text-xs text-amber-300">{t("Clip đã chọn cần tạo lại", "The selected clip needs to be generated again")}</p>}
                    <p className="text-xs text-[#9ea5b0] line-clamp-2 leading-relaxed">
                      {scene.prompt}
                    </p>

                    <div className="flex min-w-0 flex-wrap items-center justify-between pt-1 border-t border-[#2c3038]/60 text-[11px] text-[#9ea5b0]">
                      <span>{t(`${scene.duration_seconds} giây · ${purposeLabel(scene.spec?.purpose || "PRODUCT_DETAIL")}`, `${scene.duration_seconds} seconds · ${purposeLabel(scene.spec?.purpose || "PRODUCT_DETAIL")}`)}</span>
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        {hasSelectedOutput && (
                          <span className="text-emerald-400 flex items-center gap-1 font-semibold">
                            <CheckCircle2 className="w-3 h-3" />
                            <span>{t("Đã chọn clip", "Clip selected")}</span>
                          </span>
                        )}
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            enableDisableMutation.mutate({
                              sceneId: scene.id,
                              enable: !scene.enabled,
                              revision: scene.revision,
                            });
                          }}
                          className="hover:text-[#f1f3f5] underline text-[10px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
                        >
                          {scene.enabled ? t("Tắt", "Disable") : t("Bật", "Enable")}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </aside>
        </section>
      )}

      {/* Subdialogs */}
      {activeScene && isGenerateOpen && (
        <GenerationEditor
          key={`${activeScene.id}:${activeScene.revision}:${video.revision}`}
          scene={activeScene}
          scenes={scenes}
          video={video}
          onClose={() => { setServerError(null); setIsGenerateOpen(false); }}
          onSubmit={async (payload) => createGenMutation.mutateAsync({ sceneId: activeScene.id, payload })}
          isLoading={createGenMutation.isPending}
        />
      )}

      {activeScene && (
        <EditSceneDialog
          isOpen={isEditSceneOpen}
          onClose={() => setIsEditSceneOpen(false)}
          scene={activeScene}
          onSuccess={() => {
            setServerError(null);
            queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
            setIsEditSceneOpen(false);
          }}
        />
      )}

      {/* Add Scene Dialog */}
      <AddSceneDialog
        isOpen={isAddSceneOpen}
        onClose={() => setIsAddSceneOpen(false)}
        videoId={videoId}
        videoRevision={video.revision}
        sceneCount={scenes.length}
        onSuccess={() => {
            setServerError(null);
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          setIsAddSceneOpen(false);
        }}
      />

      {/* Prompt Preview Modal */}
      <Dialog
        isOpen={isPreviewPromptOpen}
        onClose={() => setIsPreviewPromptOpen(false)}
        title={t("Xem trước prompt AI", "AI prompt preview")}
        description={t("Prompt hoàn chỉnh từ mô tả ý tưởng, sản phẩm, thương hiệu và bộ cải thiện H3.", "The full prompt assembled from the creative brief, product, brand, and H3 enhancer.")}
      >
        <div className="space-y-4">
          {promptPreviewLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : promptPreviewData ? (
            <div className="space-y-4 text-xs">
              <div className="p-3 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-1">
                <span className="font-semibold text-[#9ea5b0] uppercase">{t("Prompt gốc:", "Raw prompt:")}</span>
                <p className="text-[#f1f3f5]">{promptPreviewData.raw_prompt}</p>
              </div>

              <div className="p-3 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-1">
                <span className="font-semibold text-[#9ea5b0] uppercase">{t("Prompt thực thi:", "Execution prompt:")}</span>
                <p className="text-[#f1f3f5] font-mono text-[11px] leading-relaxed">
                  {promptPreviewData.execution_prompt}
                </p>
              </div>

              {promptPreviewData.warnings?.length > 0 && (
                <div className="p-3 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-300 space-y-1">
                  <span className="font-semibold">{t("Cảnh báo:", "Warnings:")}</span>
                  <ul className="list-disc list-inside">
                    {promptPreviewData.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div className="text-center py-4 text-xs text-[#9ea5b0]">
              {t("Không thể tải bản xem trước prompt.", "Unable to load the prompt preview.")}</div>
          )}

          <div className="flex justify-end pt-2 border-t border-[#2c3038]">
            <Button size="sm" variant="secondary" onClick={() => setIsPreviewPromptOpen(false)}>
              {t("Đóng", "Close")}</Button>
          </div>
        </div>
      </Dialog>

      {/* History Variants Dialog */}
      <Dialog
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        title={t(`Lịch sử tạo clip: Cảnh #${activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}`, `Clip generation history: Scene #${activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}`)}
        description={t("Tất cả clip H3 đã tạo cho cảnh này. Xem trước và chọn clip phù hợp nhất.", "All H3 clips generated for this scene. Preview and select the best clip.")}
        maxWidth="lg"
      >
        <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
          {generationsData && generationsData.total > 20 && activeScene && <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
            <Button variant="secondary" disabled={historyPage <= 1 || genLoading} onClick={() => setHistoryPosition({ sceneId: activeScene.id, page: historyPage - 1 })}>{t("Trang trước", "Previous page")}</Button>
            <span>{t(`Trang ${historyPage} / ${Math.ceil(generationsData.total / 20)}`, `Page ${historyPage} / ${Math.ceil(generationsData.total / 20)}`)}</span>
            <Button variant="secondary" disabled={historyPage * 20 >= generationsData.total || genLoading} onClick={() => setHistoryPosition({ sceneId: activeScene.id, page: historyPage + 1 })}>{t("Trang sau", "Next page")}</Button>
          </div>}
          {historyError && <Alert variant="destructive">{uiError(historyError)}</Alert>}
          {genLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : !generationsData?.items.length ? (
            <div className="text-center py-8 text-xs text-[#9ea5b0]">
              {t("Chưa tạo clip nào cho cảnh này.", "No clips have been generated for this scene.")}</div>
          ) : (
            <div className="space-y-3">
              {generationsData.items.map((gen) => {
                const isSelected = activeScene?.selected_generation_id === gen.id;
                const isCompleted = gen.status === "COMPLETED";
                const isRunning = isActiveGenerationStatus(gen.status);

                return (
                  <div
                    key={gen.id}
                    className={`p-4 rounded-md border flex flex-col gap-3 transition-colors ${
                      isSelected && selectedState === "FRESH_SELECTION"
                        ? "border-emerald-500/60 bg-emerald-500/5"
                        : "border-[#2c3038] bg-[#0b101a]"
                    }`}
                  >
                    <div className="flex min-w-0 flex-wrap items-center justify-between gap-4">
                      <div className="space-y-1">
                        <div className="flex min-w-0 flex-wrap items-center gap-2">
                          <span className="font-semibold text-sm text-[#f1f3f5]">
                            {t(`Biến thể #${gen.generation_no}`, `Variant #${gen.generation_no}`)}
                          </span>
                          <Badge status={gen.status} />
                          <span className="text-xs text-[#9ea5b0] uppercase font-semibold">
                            ({gen.mode})
                          </span>
                          {isSelected && (
                            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full border ${selectedState === "FRESH_SELECTION" ? "text-emerald-400 border-emerald-500/30" : "text-amber-300 border-amber-500/30"}`}>
                              {t("✓ Đang chọn", "✓ Selected")}</span>
                          )}
                        </div>

                        <p className="text-xs text-[#9ea5b0]">
                          {t(`Tạo lúc: ${new Date(gen.created_at).toLocaleTimeString("vi-VN")} · ${gen.attempt_count} lần thử`, `Created: ${new Date(gen.created_at).toLocaleTimeString("en-US")} · ${gen.attempt_count} attempts`)}
                        </p>

                        {gen.error_message && (
                          <p className="text-xs text-red-400 font-semibold">{gen.error_message}</p>
                        )}
                      </div>

                      <div className="flex min-w-0 flex-wrap items-center gap-2 shrink-0">
                        {isRunning && (
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => cancelGenMutation.mutate(gen.id)}
                            isLoading={cancelGenMutation.isPending}
                          >
                            <XCircle className="w-3.5 h-3.5" />
                            <span>{t("Hủy", "Cancel")}</span>
                          </Button>
                        )}


                        {isCompleted && !isSelected && (
                          <Button
                            size="sm"
                            variant="primary"
                            onClick={() =>
                              activeScene &&
                              selectGenMutation.mutate({
                                sceneId: activeScene.id,
                                generationId: gen.id,
                                revision: activeScene.revision,
                              })
                            }
                            isLoading={selectGenMutation.isPending}
                          >
                            <Check className="w-3.5 h-3.5" />
                            <span>{t("Chọn clip này", "Select this clip")}</span>
                          </Button>
                        )}
                      </div>
                    </div>

                    <GenerationHistoryDetails generation={gen}
                      scene={scenes.find((scene) => scene.id === gen.scene_id)} scenes={scenes}
                      onRegenerate={() => regenerateMutation.mutate(gen)}
                      onVariation={() => variationMutation.mutate(gen)}
                      onReuse={() => reuseSettingsMutation.mutate(gen)}
                      busy={regenerateMutation.isPending || reuseSettingsMutation.isPending || variationMutation.isPending} />

                    {/* Media Preview for Completed Variant */}
                    {isCompleted && gen.output_asset_id && (
                      <div className="mt-1 w-full">
                        <VideoPreview assetId={gen.output_asset_id} className="max-h-56 w-full" />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </Dialog>
    </div>
  );
}

// Subcomponent: Storyboard Setup for LONG_VIDEO
function StoryboardSetupPanel({
  video,
  onSuccess,
  onOpenAddScene,
  onConflict,
}: {
  video: { id: string; target_duration: number; brief: string; revision: number; aspect_ratio: string };
  onSuccess: () => void;
  onOpenAddScene: () => void;
  onConflict?: () => void;
}) {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const purposeLabel = (purpose: string | undefined) => ({
    HOOK: t("Mở đầu cuốn hút", "Attention-grabbing opening"),
    PRODUCT_DETAIL: t("Cận cảnh sản phẩm", "Product close-up"), BENEFIT: t("Lợi ích sản phẩm", "Product benefits"),
    LIFESTYLE: t("Đời sống & trải nghiệm", "Lifestyle & experience"), CTA: t("Kêu gọi hành động", "Call to action"),
  } as Record<string, string>)[purpose ?? "PRODUCT_DETAIL"] ?? purpose ?? t("Không xác định", "Unknown");
  const [preview, setPreview] = useState<StoryboardPreviewDTO | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const publishIdempotencyKeyRef = useRef<string>(generateIdempotencyKey());

  const handlePreview = async () => {
    setIsPreviewLoading(true);
    setErrorMsg(null);
    try {
      const data = await previewStoryboard(video.id);
      setPreview(data);
      // Reset storyboard idempotency key when a new preview is generated
      publishIdempotencyKeyRef.current = generateIdempotencyKey();
    } catch (err) {
      setErrorMsg(uiError(err, t("Không thể tạo bản xem trước bảng phân cảnh.", "Unable to generate the storyboard preview.")));
    } finally {
      setIsPreviewLoading(false);
    }
  };

  const handlePublish = async () => {
    if (!preview || preview.scenes.length === 0) return;
    setIsPublishing(true);
    setErrorMsg(null);
    // Use preview.video_revision as the revision for publish!
    // Do NOT use newest video.revision for an already-generated preview.
    const revisionToUse = preview.video_revision ?? video.revision;

    try {
      await publishStoryboard(
        video.id,
        {
          scenes: preview.scenes.map((s, idx) => ({
            scene_order: idx,
            prompt: s.prompt,
            negative_prompt: s.negative_prompt || "",
            duration_seconds: s.duration_seconds,
            spec: s.spec,
          })),
        },
        revisionToUse,
        publishIdempotencyKeyRef.current
      );
      onSuccess();
    } catch (err) {
      if (isRevisionConflict(err)) {
        setErrorMsg(
          t("Video đã được cập nhật trên máy chủ. Bản xem trước bảng phân cảnh không còn hợp lệ. Vui lòng tạo bản xem trước mới.", "The video has been updated on the server. This storyboard preview is outdated. Please generate a new preview.")
        );
        // Discard stale preview, require generating a new preview
        setPreview(null);
        // Refetch latest video state
        onConflict?.();
      } else {
        setErrorMsg(uiError(err, t("Không thể lưu bảng phân cảnh.", "Unable to save the storyboard.")));
      }
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <div className="p-8 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-6 max-w-4xl mx-auto">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <LayoutGrid className="w-5 h-5 text-blue-400" />
            <h2 className="text-xl font-semibold text-[#f1f3f5]">{t("Thiết lập bảng phân cảnh", "Set up storyboard")}</h2>
          </div>
          <p className="text-xs text-[#9ea5b0] mt-1.5">
            {t(`Video dài (${video.target_duration} giây) chưa có cảnh nào. Tạo bảng phân cảnh AI từ mô tả sản phẩm, kiểm tra và lưu để bắt đầu làm việc.`, `This long video (${video.target_duration} seconds) has no scenes yet. Generate an AI storyboard from the product brief, review it, and save it to begin working.`)}
          </p>
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-2 shrink-0">
          <Button variant="secondary" size="sm" onClick={onOpenAddScene}>
            <Plus className="w-3.5 h-3.5" />
            <span>{t("Thêm cảnh thủ công", "Add scene manually")}</span>
          </Button>
          <Button variant="primary" size="sm" onClick={handlePreview} isLoading={isPreviewLoading}>
            <Sparkles className="w-3.5 h-3.5" />
            <span>{t("Tạo bảng phân cảnh AI", "Generate AI storyboard")}</span>
          </Button>
        </div>
      </div>

      {errorMsg && (
        <Alert variant="destructive" title={t("Lỗi bảng phân cảnh", "Storyboard error")}>
          {errorMsg}
        </Alert>
      )}

      {/* Brief Summary */}
      <div className="p-4 rounded-md bg-[#0b101a] border border-[#2c3038] text-xs space-y-1">
        <span className="font-semibold text-[#9ea5b0] uppercase">{t("Mô tả quảng cáo:", "Advertising brief:")}</span>
        <p className="text-[#f1f3f5] leading-relaxed">{video.brief || t("Chưa có mô tả ý tưởng.", "No creative brief yet.")}</p>
        <div className="pt-2 text-[11px] text-[#9ea5b0] flex gap-4">
          <span>{t("Thời lượng mục tiêu:", "Target duration:")} <strong className="text-white">{t(`${video.target_duration} giây`, `${video.target_duration} seconds`)}</strong></span>
          <span>{t("Tỷ lệ:", "Aspect ratio:")} <strong className="text-white">{video.aspect_ratio}</strong></span>
        </div>
      </div>

      {/* Previewed Scenes List */}
      {preview && (
        <div className="space-y-4 animate-in fade-in duration-200">
          <div className="flex min-w-0 flex-wrap items-center justify-between border-b border-[#2c3038] pb-3">
            <span className="text-xs font-semibold text-[#f1f3f5] uppercase tracking-wider">
              {t(`Bảng phân cảnh đề xuất (${preview.scenes.length} cảnh · Nguồn: ${preview.source})`, `Proposed storyboard (${preview.scenes.length} scenes · Source: ${preview.source})`)}
            </span>
            <Button
              variant="primary"
              size="sm"
              onClick={handlePublish}
              isLoading={isPublishing}
            >
              <Check className="w-4 h-4" />
              <span>{t("Duyệt & lưu bảng phân cảnh", "Approve & save storyboard")}</span>
            </Button>
          </div>

          {preview.warnings?.length > 0 && (
            <Alert variant="warning" title={t("Lưu ý khi lập bảng phân cảnh", "Storyboard planning notes")}>
              <ul className="list-disc list-inside text-xs">
                {preview.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </Alert>
          )}

          <div className="space-y-3">
            {preview.scenes.map((scene, idx) => (
              <div
                key={idx}
                className="p-4 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-1.5"
              >
                <div className="flex min-w-0 flex-wrap items-center justify-between text-xs">
                  <span className="font-semibold text-blue-400">
                    {t(`Cảnh #${idx + 1}: ${scene.title || purposeLabel(scene.purpose)}`, `Scene #${idx + 1}: ${scene.title || purposeLabel(scene.purpose)}`)}
                  </span>
                  <span className="text-[#9ea5b0]">
                    {t(`${scene.duration_seconds} giây · ${purposeLabel(scene.purpose)}`, `${scene.duration_seconds} seconds · ${purposeLabel(scene.purpose)}`)}
                  </span>
                </div>
                <p className="text-xs text-[#f1f3f5] leading-relaxed">{scene.prompt}</p>
                {scene.negative_prompt && (
                  <p className="text-[11px] text-red-400/80">
                    {t("Prompt loại trừ:", "Negative prompt:")}{scene.negative_prompt}
                  </p>
                )}
              </div>
            ))}
          </div>

          <div className="flex justify-end pt-2">
            <Button
              variant="primary"
              onClick={handlePublish}
              isLoading={isPublishing}
            >
              <Check className="w-4 h-4" />
              <span>{t("Duyệt & lưu bảng phân cảnh", "Approve & save storyboard")}</span>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}


// Subcomponent: Edit Scene Dialog (RHF + Zod)
const editSceneSchema = (t: (vi: string, en: string) => string) => z.object({
  title: z.string().max(255).optional(),
  purpose: z.string().max(80).default("PRODUCT_DETAIL"),
  continuity: z.enum(["CUT", "CONTINUOUS"]).default("CUT"),
  prompt: z.string().min(1, t("Prompt không được để trống", "Prompt is required")).max(20000),
  negative_prompt: z.string().max(10000).optional(),
  duration_seconds: z.coerce.number().min(4, t("Thời lượng từ 4 đến 15 giây", "Duration must be between 4 and 15 seconds")).max(15, t("Thời lượng từ 4 đến 15 giây", "Duration must be between 4 and 15 seconds")),
});

type EditSceneValues = z.infer<ReturnType<typeof editSceneSchema>>;

function EditSceneDialog({
  isOpen,
  onClose,
  scene,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  scene: SceneDTO;
  onSuccess: () => void;
}) {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<EditSceneValues>({
    resolver: zodResolver(editSceneSchema(t), { errorMap: () => ({ message: t("Giá trị không hợp lệ. Kiểm tra giới hạn của trường này.", "Invalid value. Check the limits for this field.") }) }),
    defaultValues: {
      continuity: scene.scene_order === 0 ? "CUT" : scene.spec?.continuity ?? "CUT",
      title: scene.spec?.title || "",
      purpose: scene.spec?.purpose || "PRODUCT_DETAIL",
      prompt: scene.prompt,
      negative_prompt: scene.negative_prompt,
      duration_seconds: scene.duration_seconds,
    },
  });

  useEffect(() => {
    reset({
      continuity: scene.scene_order === 0 ? "CUT" : scene.spec?.continuity ?? "CUT",
      title: scene.spec?.title || "",
      purpose: scene.spec?.purpose || "PRODUCT_DETAIL",
      prompt: scene.prompt,
      negative_prompt: scene.negative_prompt,
      duration_seconds: scene.duration_seconds,
    });
  }, [
    scene.duration_seconds,
    scene.id,
    scene.negative_prompt,
    scene.prompt,
    scene.revision,
    scene.spec?.purpose,
    scene.spec?.title,
    scene.spec?.continuity,
    scene.scene_order,
    reset,
  ]);

  const mutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (values: EditSceneValues) =>
      patchScene(
        scene.id,
        {
          prompt: values.prompt,
          negative_prompt: values.negative_prompt,
          duration_seconds: values.duration_seconds,
          spec: editSceneSpec(scene.spec, { title: values.title, purpose: values.purpose, continuity: values.continuity }, scene.scene_order),
        },
        scene.revision
      ),
    onSuccess: () => { setServerError(null); onSuccess(); },
    onError: (err) => setServerError(uiError(err)),
  });

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("Chỉnh sửa cảnh", "Edit scene")}
      description={t(`Cập nhật kịch bản cảnh (bản sửa đổi #${scene.revision})`, `Update the scene script (revision #${scene.revision})`)}
      maxWidth="md"
    >
      {serverError && (
        <Alert variant="destructive" title={t("Lỗi cập nhật", "Update failed")}>
          {serverError}
        </Alert>
      )}

      <form onSubmit={handleSubmit((data) => mutation.mutate(data))} className="space-y-4">
        <div>
          <Input id="scene_title" label={t("Tên cảnh", "Scene title")} {...register("title")} />
          {errors.title && <p className="text-xs text-red-400 mt-1">{errors.title.message}</p>}
        </div>

        <div>
          <label className="grid gap-1.5 text-xs" htmlFor="scene_continuity">{t("Nối tiếp cảnh trước", "Continuity from the previous scene")}<select id="scene_continuity" {...register("continuity")} className="rounded-md border border-[#2c3038] bg-[#0b101a] p-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
              <option value="CUT">{t("Cảnh độc lập", "Independent scene")}</option>
              {scene.scene_order > 0 && <option value="CONTINUOUS">{t("Nối tiếp cảnh trước", "Continue from the previous scene")}</option>}
            </select>
            {scene.scene_order === 0 && <span>{t("Cảnh đầu tiên luôn độc lập.", "The first scene is always independent.")}</span>}
          </label>
          <Textarea id="scene_prompt" label={t("Prompt hành động & góc quay *", "Action & camera angle prompt *")} {...register("prompt")} />
          {errors.prompt && <p className="text-xs text-red-400 mt-1">{errors.prompt.message}</p>}
        </div>

        <div>
          <Input id="neg_prompt" label={t("Prompt loại trừ", "Negative prompt")} {...register("negative_prompt")} />
          {errors.negative_prompt && <p className="text-xs text-red-400 mt-1">{errors.negative_prompt.message}</p>}
        </div>

        <div className="grid responsive-field-grid gap-4">
          <div>
            <Input
              id="scene_duration"
              label={t("Thời lượng (4–15 giây) *", "Duration (4–15 seconds) *")}
              type="number"
              min={4}
              max={15}
              {...register("duration_seconds")}
            />
            {errors.duration_seconds && (
              <p className="text-xs text-red-400 mt-1">{errors.duration_seconds.message}</p>
            )}
          </div>

          <div className="grid gap-1.5">
            <label htmlFor="scene_purpose" className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
              {t("Mục đích cảnh:", "Scene purpose:")}</label>
            <select
              id="scene_purpose"
              {...register("purpose")}
              className="rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
            >
              <option value="HOOK">{t("Mở đầu cuốn hút", "Attention-grabbing opening")}</option>
              <option value="PRODUCT_DETAIL">{t("Cận cảnh sản phẩm", "Product close-up")}</option>
              <option value="BENEFIT">{t("Lợi ích sản phẩm", "Product benefits")}</option>
              <option value="LIFESTYLE">{t("Đời sống & trải nghiệm", "Lifestyle & experience")}</option>
              <option value="CTA">{t("Kêu gọi hành động", "Call to action")}</option>
            </select>
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-3 pt-4 border-t border-[#2c3038]">
          <Button type="button" variant="outline" onClick={onClose}>
            {t("Hủy", "Cancel")}</Button>
          <Button type="submit" variant="primary" isLoading={mutation.isPending}>
            {t("Lưu thay đổi", "Save changes")}</Button>
        </div>
      </form>
    </Dialog>
  );
}

// Subcomponent: Add Scene Dialog (RHF + Zod)
const addSceneSchema = (t: (vi: string, en: string) => string) => z.object({
  title: z.string().max(255).optional(),
  purpose: z.string().max(80).default("PRODUCT_DETAIL"),
  continuity: z.enum(["CUT", "CONTINUOUS"]).default("CUT"),
  prompt: z.string().min(1, t("Prompt không được để trống", "Prompt is required")).max(20000),
  negative_prompt: z.string().max(10000).optional(),
  duration_seconds: z.coerce.number().min(4, t("Thời lượng từ 4 đến 15 giây", "Duration must be between 4 and 15 seconds")).max(15, t("Thời lượng từ 4 đến 15 giây", "Duration must be between 4 and 15 seconds")).default(5),
});

type AddSceneValues = z.infer<ReturnType<typeof addSceneSchema>>;

function AddSceneDialog({
  isOpen,
  onClose,
  videoId,
  videoRevision,
  sceneCount,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  videoId: string;
  videoRevision: number;
  sceneCount: number;
  onSuccess: () => void;
}) {
  const { t } = useI18n();
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<AddSceneValues>({
    resolver: zodResolver(addSceneSchema(t), { errorMap: () => ({ message: t("Giá trị không hợp lệ. Kiểm tra giới hạn của trường này.", "Invalid value. Check the limits for this field.") }) }),
    defaultValues: {
      title: "",
      continuity: "CUT",
      purpose: "PRODUCT_DETAIL",
      prompt: "",
      negative_prompt: "",
      duration_seconds: 5,
    },
  });

  const mutation = useMutation({
    onMutate: () => setServerError(null),
    mutationFn: (values: AddSceneValues) =>
      createScene(
        videoId,
        {
          prompt: values.prompt,
          negative_prompt: values.negative_prompt,
          duration_seconds: values.duration_seconds,
          spec: {
            title: values.title || t("Cảnh mới", "New scene"),
            purpose: values.purpose,
            description: values.prompt,
            subject: "",
            action: "",
            environment: "",
            camera: "cinematic",
            lighting: "clean",
            style: "commercial",
            continuity: sceneCount === 0 ? "CUT" : values.continuity,
          },
        },
        videoRevision
      ),
    onSuccess: () => {
      setServerError(null);
      reset();
      onSuccess();
    },
    onError: (err) => setServerError(uiError(err)),
  });

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("Thêm cảnh mới", "Add new scene")}
      description={t("Tạo thêm cảnh thủ công cho video dài.", "Add a scene manually to the long video.")}
      maxWidth="md"
    >
      {serverError && (
        <Alert variant="destructive" title={t("Không thể thêm cảnh", "Unable to add scene")}>
          {serverError}
        </Alert>
      )}

      <form onSubmit={handleSubmit((data) => mutation.mutate(data))} className="space-y-4">
        <div>
          <Input id="add_scene_title" label={t("Tên cảnh", "Scene title")} placeholder={t("Cận cảnh tính năng...", "Close-up of a feature...")} {...register("title")} />
          {errors.title && <p className="text-xs text-red-400 mt-1">{errors.title.message}</p>}
        </div>

        <div>
          <label className="grid gap-1.5 text-xs" htmlFor="add_scene_continuity">{t("Nối tiếp cảnh trước", "Continuity from the previous scene")}<select id="add_scene_continuity" {...register("continuity")} className="rounded-md border border-[#2c3038] bg-[#0b101a] p-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">
              <option value="CUT">{t("Cảnh độc lập", "Independent scene")}</option>
              {sceneCount > 0 && <option value="CONTINUOUS">{t("Nối tiếp cảnh trước", "Continue from the previous scene")}</option>}
            </select>
            {sceneCount === 0 && <span>{t("Cảnh đầu tiên luôn độc lập.", "The first scene is always independent.")}</span>}
          </label>
          <Textarea
            id="add_scene_prompt"
            label={t("Prompt hành động & góc quay *", "Action & camera angle prompt *")}
            placeholder={t("Góc quay điện ảnh 4K cận cảnh sản phẩm...", "Cinematic 4K close-up of the product...")}
            {...register("prompt")}
          />
          {errors.prompt && <p className="text-xs text-red-400 mt-1">{errors.prompt.message}</p>}
        </div>

        <div>
          <Input id="add_neg_prompt" label={t("Prompt loại trừ", "Negative prompt")} placeholder={t("mờ, biến dạng...", "blurry, distorted...")} {...register("negative_prompt")} />
          {errors.negative_prompt && <p className="text-xs text-red-400 mt-1">{errors.negative_prompt.message}</p>}
        </div>

        <div className="grid responsive-field-grid gap-4">
          <div>
            <Input
              id="add_scene_duration"
              label={t("Thời lượng (4–15 giây) *", "Duration (4–15 seconds) *")}
              type="number"
              min={4}
              max={15}
              {...register("duration_seconds")}
            />
            {errors.duration_seconds && (
              <p className="text-xs text-red-400 mt-1">{errors.duration_seconds.message}</p>
            )}
          </div>

          <div className="grid gap-1.5">
            <label htmlFor="add_scene_purpose" className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
              {t("Mục đích cảnh:", "Scene purpose:")}</label>
            <select
              id="add_scene_purpose"
              {...register("purpose")}
              className="rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
            >
              <option value="HOOK">{t("Mở đầu cuốn hút", "Attention-grabbing opening")}</option>
              <option value="PRODUCT_DETAIL">{t("Cận cảnh sản phẩm", "Product close-up")}</option>
              <option value="BENEFIT">{t("Lợi ích sản phẩm", "Product benefits")}</option>
              <option value="LIFESTYLE">{t("Đời sống & trải nghiệm", "Lifestyle & experience")}</option>
              <option value="CTA">{t("Kêu gọi hành động", "Call to action")}</option>
            </select>
          </div>
        </div>

        <div className="flex flex-wrap justify-end gap-3 pt-4 border-t border-[#2c3038]">
          <Button type="button" variant="outline" onClick={onClose}>
            {t("Hủy", "Cancel")}</Button>
          <Button type="submit" variant="primary" isLoading={mutation.isPending}>
            {t("Thêm cảnh", "Add scene")}</Button>
        </div>
      </form>
    </Dialog>
  );
}
