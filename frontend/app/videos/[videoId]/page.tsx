"use client";

import React, { use, useState, useRef, useMemo, useEffect } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
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
  cancelGeneration,
} from "@/lib/api/generations";
import { listAssets } from "@/lib/api/assets";
import { generateIdempotencyKey } from "@/lib/api/client";
import { queryKeys } from "@/lib/query/query-keys";
import { useVideoEvents } from "@/lib/hooks/use-video-events";
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { getProgressPercent } from "@/lib/utils/progress";
import { useIdempotentAction } from "@/lib/hooks/use-idempotent-action";
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
import type {
  GenerationDTO,
  SceneDTO,
  StoryboardPreviewDTO,
  GenerationRequest,
  VariationRequest,
  GenerationInputAssetRole,
  GenerationMode,
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

function asGenerationMode(value: unknown): GenerationMode | null {
  return value === "t2v" || value === "i2v" || value === "i2v_first_last" || value === "r2v"
    ? value
    : null;
}

function snapshotAssetIds(generation: GenerationDTO, role: GenerationInputAssetRole): string[] {
  return (generation.input_snapshot.assets ?? [])
    .filter((asset) => asset.role === role)
    .sort((left, right) => (left.order_index ?? 0) - (right.order_index ?? 0))
    .map((asset) => asset.id);
}

function snapshotNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function buildVariationPayload(parent: GenerationDTO): VariationRequest {
  const mode = asGenerationMode(parent.input_snapshot.mode) ?? asGenerationMode(parent.mode);
  const payload: VariationRequest = {
    parent_generation_id: parent.id,
    workflow_id: parent.workflow_id,
    mode,
  };

  const width = snapshotNumber(parent.input_snapshot.width);
  const height = snapshotNumber(parent.input_snapshot.height);
  const steps = snapshotNumber(parent.input_snapshot.steps);

  if (width !== undefined && height !== undefined) {
    payload.width = width;
    payload.height = height;
  }
  if (steps !== undefined) {
    payload.steps = steps;
  }

  if (mode === "i2v") {
    payload.first_frame_asset_id = snapshotAssetIds(parent, "FIRST_FRAME")[0] ?? null;
  } else if (mode === "i2v_first_last") {
    payload.first_frame_asset_id = snapshotAssetIds(parent, "FIRST_FRAME")[0] ?? null;
    payload.last_frame_asset_id = snapshotAssetIds(parent, "LAST_FRAME")[0] ?? null;
  } else if (mode === "r2v") {
    payload.reference_image_asset_ids = snapshotAssetIds(parent, "REFERENCE_IMAGE");
    payload.reference_video_asset_ids = snapshotAssetIds(parent, "REFERENCE_VIDEO");
    payload.reference_audio_asset_ids = snapshotAssetIds(parent, "REFERENCE_AUDIO");
  }

  return payload;
}

export default function VideoWorkspacePage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
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
  const genAction = useIdempotentAction<GenerationRequest>();
  const generateAllKeyRef = useRef<string>(generateIdempotencyKey());
  const variationAction = useIdempotentAction<VariationRequest>();

  // SSE real-time hook
  const { isConnected, generationProgress, sceneActiveGeneration } = useVideoEvents(videoId);

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

  // Fetch generations for the selected scene
  const { data: generationsData, isLoading: genLoading } = useQuery({
    queryKey: queryKeys.scenes.generations(activeScene?.id || ""),
    queryFn: () => (activeScene ? listSceneGenerations(activeScene.id, { size: 20 }) : null),
    enabled: !!activeScene,
  });

  // Prompt Preview Query
  const {
    data: promptPreviewData,
    isLoading: promptPreviewLoading,
    refetch: fetchPromptPreview,
  } = useQuery({
    queryKey: queryKeys.scenes.promptPreview(activeScene?.id || ""),
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
        const matchingGen = generationsData?.items.find((g) => g.id === sseGenId);
        const percent = getProgressPercent(live.progress, matchingGen?.progress_current, matchingGen?.progress_total);
        return { id: sseGenId, progress: { ...live, progress: percent } };
      }
    }

    // 2. Check running items in generationsData
    const running = generationsData?.items.find((g) => isActiveGenerationStatus(g.status));
    if (running) {
      const live = generationProgress[running.id];
      const livePercent = live?.progress;
      const percent = getProgressPercent(livePercent, running.progress_current, running.progress_total);
      return {
        id: running.id,
        progress: {
          status: live?.status || running.status,
          stage: live?.stage || running.phase || "Processing",
          progress: percent,
        },
      };
    }

    return null;
  }, [activeScene, sceneActiveGeneration, generationProgress, generationsData?.items]);

  // Mutations
  const reorderMutation = useMutation({
    mutationFn: (sceneIds: string[]) => {
      if (!video) throw new Error("Video not loaded");
      return reorderScenes(videoId, { scene_ids: sceneIds }, video.revision);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const enableDisableMutation = useMutation({
    mutationFn: ({ sceneId, enable, revision }: { sceneId: string; enable: boolean; revision: number }) =>
      enable ? enableScene(sceneId, revision) : disableScene(sceneId, revision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const selectGenMutation = useMutation({
    mutationFn: ({ sceneId, generationId, revision }: { sceneId: string; generationId: string; revision: number }) =>
      selectGeneration(sceneId, { generation_id: generationId }, revision),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      setIsHistoryOpen(false);
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const cancelGenMutation = useMutation({
    mutationFn: (genId: string) => cancelGeneration(genId),
    onSuccess: () => {
      if (activeScene) {
        queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(activeScene.id) });
      }
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const createGenMutation = useMutation({
    mutationFn: (payload: GenerationRequest) => {
      if (!activeScene) throw new Error("No active scene");
      const key = genAction.getKey(payload);
      return createGeneration(activeScene.id, payload, key);
    },
    onSuccess: () => {
      genAction.reset();
      if (activeScene) {
        queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(activeScene.id) });
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
      setIsGenerateOpen(false);
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const variationMutation = useMutation({
    mutationFn: (parentGeneration: GenerationDTO) => {
      if (!activeScene) throw new Error("No active scene");
      const payload = buildVariationPayload(parentGeneration);
      const key = variationAction.getKey(payload);
      return createVariation(activeScene.id, payload, key);
    },
    onSuccess: () => {
      variationAction.reset();
      if (activeScene) {
        queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(activeScene.id) });
      }
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  const generateAllMutation = useMutation({
    mutationFn: () => generateAll(videoId, {}, generateAllKeyRef.current),
    onSuccess: () => {
      generateAllKeyRef.current = generateIdempotencyKey();
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
    },
    onError: (err) => setServerError(getErrorMessage(err)),
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
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <Skeleton className="h-96 lg:col-span-2" />
          <Skeleton className="h-96" />
        </div>
      </div>
    );
  }

  if (videoError || !video) {
    return (
      <div className="space-y-4">
        <Alert variant="destructive" title="Không tìm thấy video">
          {getErrorMessage(videoError, "Video không tồn tại hoặc đã bị xóa.")}
        </Alert>
        <Link href="/videos" className="secondary-action inline-flex items-center gap-2">
          <ArrowLeft className="w-4 h-4" />
          <span>Quay lại danh sách video</span>
        </Link>
      </div>
    );
  }

  const selectedGen = generationsData?.items.find((g) => g.id === activeScene?.selected_generation_id);

  return (
    <div className="space-y-8">
      {/* Top Header */}
      <div>
        <div className="flex items-center justify-between gap-4 mb-3">
          <Link
            href="/videos"
            className="text-xs text-[#9ea5b0] hover:text-[#f1f3f5] inline-flex items-center gap-1.5"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
            <span>Tất cả video</span>
          </Link>

          <div className="flex items-center gap-2 text-xs">
            <span
              className={`w-2 h-2 rounded-full ${
                isConnected ? "bg-emerald-500 " : "bg-zinc-600"
              }`}
            />
            <span className="text-[#9ea5b0]">
              {isConnected ? "SSE Live Connected" : "SSE Connecting..."}
            </span>
          </div>
        </div>

        <PageHeader
          eyebrow={`Workspace Storyboard · Rev #${video.revision}`}
          title={video.title}
          description={`${video.kind === "QUICK_CLIP" ? "Quick Clip" : "Long Video"} · Tỷ lệ ${video.aspect_ratio} · Tổng ${video.target_duration}s · Trạng thái: ${video.status}`}
        >
          <div className="flex items-center gap-2 flex-wrap">
            {scenes.length > 0 && (
              <Button
                variant="secondary"
                size="sm"
                onClick={() => generateAllMutation.mutate()}
                isLoading={generateAllMutation.isPending}
              >
                <Sparkles className="w-4 h-4 text-blue-400" />
                <span>Generate toàn bộ</span>
              </Button>
            )}

            <Link
              href={`/videos/${video.id}/assembly`}
              className="primary-action text-xs flex items-center gap-1.5"
            >
              <Film className="w-4 h-4" />
              <span>Chuyển sang Assembly</span>
            </Link>
          </div>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title="Thông báo hệ thống">
          <div className="flex items-center justify-between gap-4">
            <span>{serverError}</span>
            <Button size="sm" variant="secondary" onClick={() => refetchVideo()}>
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Làm mới</span>
            </Button>
          </div>
        </Alert>
      )}

      {/* When LONG_VIDEO has no scenes: render Storyboard Setup */}
      {video.kind === "LONG_VIDEO" && scenes.length === 0 ? (
        <StoryboardSetupPanel
          video={video}
          onSuccess={() => {
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
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-[11px] font-semibold text-blue-400 uppercase tracking-wider">
                    Scene #{activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}
                  </span>
                  <h2 className="text-lg font-semibold text-[#f1f3f5] mt-0.5">
                    {activeScene?.spec?.title || `Scene ${activeScene?.scene_order || 0}`}
                  </h2>
                </div>

                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      setIsPreviewPromptOpen(true);
                      fetchPromptPreview();
                    }}
                  >
                    <Eye className="w-3.5 h-3.5" />
                    <span>Prompt AI</span>
                  </Button>

                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setIsEditSceneOpen(true)}
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                    <span>Sửa Scene</span>
                  </Button>

                  <Button
                    size="sm"
                    variant="primary"
                    onClick={() => {
                      genAction.reset();
                      setIsGenerateOpen(true);
                    }}
                  >
                    <Play className="w-3.5 h-3.5" />
                    <span>Generate Clip</span>
                  </Button>
                </div>
              </div>

              {/* Video / Generation Preview Display */}
              <div className="preview-frame relative flex flex-col items-center justify-center p-6 text-center min-h-[320px]">
                {activeScene && activeSceneGeneration?.progress ? (
                  <div className="w-full max-w-md space-y-3 p-4 rounded-md bg-[#181a1e]/90 border border-[#2c3038]">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-semibold text-[#f1f3f5]">
                        {activeSceneGeneration.progress.stage || "Processing"}
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
                      <span className="text-xs font-semibold text-emerald-400 uppercase tracking-wider block">
                        ✓ Generation #{selectedGen.generation_no} đã chọn
                      </span>
                      <p className="text-xs text-[#9ea5b0] mt-1 max-w-sm mx-auto">
                        Mode: {selectedGen.mode} · Status: {selectedGen.status}
                      </p>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => setIsHistoryOpen(true)}>
                      Xem tất cả variants ({generationsData?.total ?? 0})
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-3 max-w-sm">
                    <div className="w-12 h-12 rounded-md bg-[#22252b] border border-[#2c3038] flex items-center justify-center text-[#9ea5b0] mx-auto">
                      <Film className="w-6 h-6" />
                    </div>
                    <p className="text-xs text-[#9ea5b0]">
                      Chưa có generation nào được chọn cho scene này. Nhấn &quot;Generate Clip&quot; để tạo video bằng H3 engine.
                    </p>
                    {generationsData?.items.length ? (
                      <Button size="sm" variant="secondary" onClick={() => setIsHistoryOpen(true)}>
                        Chọn từ {generationsData.total} variants đã sinh
                      </Button>
                    ) : null}
                  </div>
                )}
              </div>

              {/* Scene Prompt & Spec Summary */}
              <div className="p-4 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-[#9ea5b0] font-semibold uppercase tracking-wider">
                    Scene Prompt:
                  </span>
                  <span className="text-[#9ea5b0]">
                    Thời lượng: {activeScene?.duration_seconds}s · Rev #{activeScene?.revision}
                  </span>
                </div>
                <p className="text-xs text-[#f1f3f5] leading-relaxed">
                  {activeScene?.prompt || "Chưa có prompt."}
                </p>
                {activeScene?.negative_prompt && (
                  <p className="text-[11px] text-red-400/80">
                    Negative: {activeScene.negative_prompt}
                  </p>
                )}
              </div>
            </div>
          </div>

          {/* Right: Scene Timeline & Ordering Sidebar */}
          <aside className="timeline-panel space-y-4">
            <div className="flex items-center justify-between">
              <h2>Kịch bản phân cảnh ({scenes.length})</h2>
              {video.kind === "LONG_VIDEO" && scenes.length < 15 && (
                <Button size="sm" variant="outline" onClick={() => setIsAddSceneOpen(true)}>
                  <Plus className="w-3.5 h-3.5" />
                  <span>Thêm scene</span>
                </Button>
              )}
            </div>

            <div className="space-y-3">
              {scenes.map((scene, idx) => {
                const isSelected = activeScene?.id === scene.id;
                const hasSelectedOutput = !!scene.selected_generation_id;

                return (
                  <div
                    key={scene.id}
                    onClick={() => setSelectedSceneId(scene.id)}
                    className={`p-3.5 rounded-md border transition-all cursor-pointer space-y-2 ${
                      isSelected
                        ? "border-blue-500 bg-blue-500/10 "
                        : "border-[#2c3038] bg-[#181a1e] hover:border-[#2a2e37]"
                    } ${!scene.enabled ? "opacity-50" : ""}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="text-xs font-semibold text-[#9ea5b0]">#{idx + 1}</span>
                        <h3 className="font-semibold text-xs text-[#f1f3f5] truncate">
                          {scene.spec?.title || `Scene ${idx + 1}`}
                        </h3>
                        {!scene.enabled && (
                          <span className="text-[10px] text-red-400 font-semibold">(Tắt)</span>
                        )}
                      </div>

                      <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                        {video.kind === "LONG_VIDEO" && (
                          <>
                            <button
                              disabled={idx === 0}
                              onClick={() => handleMove(idx, "up")}
                              className="p-1 rounded text-[#9ea5b0] hover:text-white disabled:opacity-20"
                              title="Di chuyển lên"
                            >
                              <ArrowUp className="w-3.5 h-3.5" />
                            </button>
                            <button
                              disabled={idx === scenes.length - 1}
                              onClick={() => handleMove(idx, "down")}
                              className="p-1 rounded text-[#9ea5b0] hover:text-white disabled:opacity-20"
                              title="Di chuyển xuống"
                            >
                              <ArrowDown className="w-3.5 h-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                    <p className="text-xs text-[#9ea5b0] line-clamp-2 leading-relaxed">
                      {scene.prompt}
                    </p>

                    <div className="flex items-center justify-between pt-1 border-t border-[#2c3038]/60 text-[11px] text-[#9ea5b0]">
                      <span>{scene.duration_seconds}s · {scene.spec?.purpose || "PRODUCT_DETAIL"}</span>
                      <div className="flex items-center gap-2">
                        {hasSelectedOutput && (
                          <span className="text-emerald-400 flex items-center gap-1 font-semibold">
                            <CheckCircle2 className="w-3 h-3" />
                            <span>Đã chọn clip</span>
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
                          className="hover:text-[#f1f3f5] underline text-[10px]"
                        >
                          {scene.enabled ? "Vô hiệu" : "Kích hoạt"}
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
      {activeScene && (
        <GenerationConfigDialog
          isOpen={isGenerateOpen}
          onClose={() => setIsGenerateOpen(false)}
          onSubmit={async (payload) => createGenMutation.mutateAsync(payload)}
          isLoading={createGenMutation.isPending}
          aspectRatio={video.aspect_ratio}
          productId={video.product_id}
          projectId={video.project_id}
        />
      )}

      {activeScene && (
        <EditSceneDialog
          isOpen={isEditSceneOpen}
          onClose={() => setIsEditSceneOpen(false)}
          scene={activeScene}
          onSuccess={() => {
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
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          setIsAddSceneOpen(false);
        }}
      />

      {/* Prompt Preview Modal */}
      <Dialog
        isOpen={isPreviewPromptOpen}
        onClose={() => setIsPreviewPromptOpen(false)}
        title="Prompt AI Engine Preview"
        description="Bản compose hoàn chỉnh từ Brief, Sản phẩm, Thương hiệu và H3 Enhancer."
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
                <span className="font-semibold text-[#9ea5b0] uppercase">Raw Prompt:</span>
                <p className="text-[#f1f3f5]">{promptPreviewData.raw_prompt}</p>
              </div>

              <div className="p-3 rounded-md bg-[#0b101a] border border-[#2c3038] space-y-1">
                <span className="font-semibold text-[#9ea5b0] uppercase">Execution Prompt:</span>
                <p className="text-[#f1f3f5] font-mono text-[11px] leading-relaxed">
                  {promptPreviewData.execution_prompt}
                </p>
              </div>

              {promptPreviewData.warnings?.length > 0 && (
                <div className="p-3 rounded-md bg-amber-500/10 border border-amber-500/30 text-amber-300 space-y-1">
                  <span className="font-semibold">Cảnh báo:</span>
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
              Không thể tải bản xem trước prompt.
            </div>
          )}

          <div className="flex justify-end pt-2 border-t border-[#2c3038]">
            <Button size="sm" variant="secondary" onClick={() => setIsPreviewPromptOpen(false)}>
              Đóng
            </Button>
          </div>
        </div>
      </Dialog>

      {/* History Variants Dialog */}
      <Dialog
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        title={`Lịch sử Generation: Scene #${activeScene?.scene_order !== undefined ? activeScene.scene_order + 1 : 1}`}
        description="Tất cả các bản kết xuất H3 từ scene này. Bạn có thể xem trước clip và chọn clip ưng ý nhất."
        maxWidth="lg"
      >
        <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
          {genLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          ) : !generationsData?.items.length ? (
            <div className="text-center py-8 text-xs text-[#9ea5b0]">
              Chưa có generation nào được tạo cho scene này.
            </div>
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
                      isSelected
                        ? "border-emerald-500/60 bg-emerald-500/5"
                        : "border-[#2c3038] bg-[#0b101a]"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-sm text-[#f1f3f5]">
                            Variant #{gen.generation_no}
                          </span>
                          <Badge status={gen.status} />
                          <span className="text-xs text-[#9ea5b0] uppercase font-semibold">
                            ({gen.mode})
                          </span>
                          {isSelected && (
                            <span className="text-xs font-semibold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full border border-emerald-500/30">
                              ✓ Đang chọn
                            </span>
                          )}
                        </div>

                        <p className="text-xs text-[#9ea5b0]">
                          Tạo lúc: {new Date(gen.created_at).toLocaleTimeString("vi-VN")} · {gen.attempt_count} attempt(s)
                        </p>

                        {gen.error_message && (
                          <p className="text-xs text-red-400 font-semibold">{gen.error_message}</p>
                        )}
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        {isRunning && (
                          <Button
                            size="sm"
                            variant="danger"
                            onClick={() => cancelGenMutation.mutate(gen.id)}
                            isLoading={cancelGenMutation.isPending}
                          >
                            <XCircle className="w-3.5 h-3.5" />
                            <span>Hủy</span>
                          </Button>
                        )}

                        {isCompleted && (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() =>
                              variationMutation.mutate(gen)
                            }
                            isLoading={variationMutation.isPending}
                            title="Tạo biến thể từ clip này"
                          >
                            <Sparkles className="w-3.5 h-3.5" />
                            <span>Tạo biến thể</span>
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
                            <span>Chọn clip này</span>
                          </Button>
                        )}
                      </div>
                    </div>

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
      setErrorMsg(getErrorMessage(err, "Không thể tạo bản xem trước storyboard."));
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
          "Video đã bị cập nhật phiên bản trên máy chủ (Revision Conflict 412). Bản xem trước storyboard hiện tại không còn hợp lệ. Vui lòng tạo lại Storyboard Preview mới."
        );
        // Discard stale preview, require generating a new preview
        setPreview(null);
        // Refetch latest video state
        onConflict?.();
      } else {
        setErrorMsg(getErrorMessage(err, "Xuất bản storyboard thất bại."));
      }
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <div className="p-8 rounded-md border border-[#2c3038] bg-[#181a1e] space-y-6 max-w-4xl mx-auto">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <LayoutGrid className="w-5 h-5 text-blue-400" />
            <h2 className="text-xl font-semibold text-[#f1f3f5]">Thiết lập Kịch bản Phân cảnh (Storyboard Setup)</h2>
          </div>
          <p className="text-xs text-[#9ea5b0] mt-1.5">
            Video dài ({video.target_duration}s) chưa có phân cảnh nào. Hãy tạo kịch bản AI tự động từ brief sản phẩm, kiểm tra và xuất bản để kích hoạt workspace.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Button variant="secondary" size="sm" onClick={onOpenAddScene}>
            <Plus className="w-3.5 h-3.5" />
            <span>Thêm scene thủ công</span>
          </Button>
          <Button variant="primary" size="sm" onClick={handlePreview} isLoading={isPreviewLoading}>
            <Sparkles className="w-3.5 h-3.5" />
            <span>Tạo Storyboard AI</span>
          </Button>
        </div>
      </div>

      {errorMsg && (
        <Alert variant="destructive" title="Lỗi Storyboard">
          {errorMsg}
        </Alert>
      )}

      {/* Brief Summary */}
      <div className="p-4 rounded-md bg-[#0b101a] border border-[#2c3038] text-xs space-y-1">
        <span className="font-semibold text-[#9ea5b0] uppercase">Brief quảng cáo:</span>
        <p className="text-[#f1f3f5] leading-relaxed">{video.brief || "Chưa có brief mô tả."}</p>
        <div className="pt-2 text-[11px] text-[#9ea5b0] flex gap-4">
          <span>Thời lượng mục tiêu: <strong className="text-white">{video.target_duration}s</strong></span>
          <span>Tỷ lệ: <strong className="text-white">{video.aspect_ratio}</strong></span>
        </div>
      </div>

      {/* Previewed Scenes List */}
      {preview && (
        <div className="space-y-4 animate-in fade-in duration-200">
          <div className="flex items-center justify-between border-b border-[#2c3038] pb-3">
            <span className="text-xs font-semibold text-[#f1f3f5] uppercase tracking-wider">
              Kịch bản đề xuất ({preview.scenes.length} phân cảnh · Nguồn: {preview.source})
            </span>
            <Button
              variant="primary"
              size="sm"
              onClick={handlePublish}
              isLoading={isPublishing}
            >
              <Check className="w-4 h-4" />
              <span>Duyệt &amp; Xuất bản Storyboard</span>
            </Button>
          </div>

          {preview.warnings?.length > 0 && (
            <Alert variant="warning" title="Lưu ý từ Storyboard Planner">
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
                <div className="flex items-center justify-between text-xs">
                  <span className="font-semibold text-blue-400">
                    Phân cảnh #{idx + 1}: {scene.title || scene.purpose}
                  </span>
                  <span className="text-[#9ea5b0]">
                    {scene.duration_seconds}s · {scene.purpose}
                  </span>
                </div>
                <p className="text-xs text-[#f1f3f5] leading-relaxed">{scene.prompt}</p>
                {scene.negative_prompt && (
                  <p className="text-[11px] text-red-400/80">
                    Negative: {scene.negative_prompt}
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
              <span>Duyệt &amp; Xuất bản Storyboard</span>
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// Subcomponent: Generation Config Dialog with RHF + Zod + Asset Pickers
const genConfigSchema = z
  .object({
    mode: z.enum(["t2v", "i2v", "i2v_first_last", "r2v"]),
    steps: z.coerce.number().min(1, "Steps tối thiểu là 1").max(100, "Steps tối đa là 100"),
    seed: z.union([z.coerce.number().int().min(0), z.literal(""), z.undefined()]).optional(),
    first_frame_asset_id: z.string().optional(),
    last_frame_asset_id: z.string().optional(),
    reference_image_asset_ids: z.array(z.string()).default([]),
    reference_video_asset_ids: z.array(z.string()).default([]),
    reference_audio_asset_ids: z.array(z.string()).default([]),
  })
  .superRefine((data, ctx) => {
    if (data.mode === "i2v") {
      if (!data.first_frame_asset_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "I2V yêu cầu chọn một hình ảnh khung hình đầu (first frame)",
          path: ["first_frame_asset_id"],
        });
      }
    } else if (data.mode === "i2v_first_last") {
      if (!data.first_frame_asset_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Vui lòng chọn hình ảnh khung hình đầu (first frame)",
          path: ["first_frame_asset_id"],
        });
      }
      if (!data.last_frame_asset_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Vui lòng chọn hình ảnh khung hình cuối (last frame)",
          path: ["last_frame_asset_id"],
        });
      }
    } else if (data.mode === "r2v") {
      const totalRefs =
        data.reference_image_asset_ids.length +
        data.reference_video_asset_ids.length +
        data.reference_audio_asset_ids.length;
      if (totalRefs === 0) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "R2V yêu cầu chọn ít nhất một reference asset (hình ảnh, video hoặc audio)",
          path: ["reference_image_asset_ids"],
        });
      }
      if (data.reference_image_asset_ids.length > 9) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Tối đa 9 reference images",
          path: ["reference_image_asset_ids"],
        });
      }
      if (data.reference_video_asset_ids.length > 3) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Tối đa 3 reference videos",
          path: ["reference_video_asset_ids"],
        });
      }
      if (data.reference_audio_asset_ids.length > 3) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Tối đa 3 reference audio",
          path: ["reference_audio_asset_ids"],
        });
      }
    }
  });

type GenConfigValues = z.infer<typeof genConfigSchema>;

function GenerationConfigDialog({
  isOpen,
  onClose,
  onSubmit,
  isLoading,
  aspectRatio,
  productId,
  projectId,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (payload: GenerationRequest) => Promise<unknown>;
  isLoading: boolean;
  aspectRatio: string;
  productId?: string | null;
  projectId?: string | null;
}) {
  const { data: assetsData, isLoading: assetsLoading } = useQuery({
    queryKey: queryKeys.assets.list({
      size: 100,
      project_id: projectId || undefined,
      product_id: productId || undefined,
      status: "READY",
    }),
    queryFn: () =>
      listAssets({
        size: 100,
        project_id: projectId || undefined,
        product_id: productId || undefined,
        status: "READY",
      }),
    enabled: isOpen,
  });

  const {
    register,
    control,
    handleSubmit,
    setValue,
    formState: { errors },
  } = useForm<GenConfigValues>({
    resolver: zodResolver(genConfigSchema),
    defaultValues: {
      mode: "t2v",
      steps: 8,
      seed: "",
      first_frame_asset_id: "",
      last_frame_asset_id: "",
      reference_image_asset_ids: [],
      reference_video_asset_ids: [],
      reference_audio_asset_ids: [],
    },
  });

  const selectedMode = useWatch({ control, name: "mode" });
  const selectedRefImages = useWatch({ control, name: "reference_image_asset_ids" }) || [];
  const selectedRefVideos = useWatch({ control, name: "reference_video_asset_ids" }) || [];
  const selectedRefAudio = useWatch({ control, name: "reference_audio_asset_ids" }) || [];

  const readyImageAssets = useMemo(
    () =>
      assetsData?.items.filter(
        (a) =>
          a.status === "READY" &&
          a.content_type.startsWith("image/") &&
          (!projectId && !productId
            ? true
            : (projectId && a.project_id === projectId) ||
              (productId && a.product_id === productId) ||
              (!a.project_id && !a.product_id))
      ) || [],
    [assetsData?.items, projectId, productId]
  );

  const readyVideoAssets = useMemo(
    () =>
      assetsData?.items.filter(
        (a) =>
          a.status === "READY" &&
          a.content_type.startsWith("video/") &&
          (!projectId && !productId
            ? true
            : (projectId && a.project_id === projectId) ||
              (productId && a.product_id === productId) ||
              (!a.project_id && !a.product_id))
      ) || [],
    [assetsData?.items, projectId, productId]
  );

  const readyAudioAssets = useMemo(
    () =>
      assetsData?.items.filter(
        (a) =>
          a.status === "READY" &&
          a.content_type.startsWith("audio/") &&
          (!projectId && !productId
            ? true
            : (projectId && a.project_id === projectId) ||
              (productId && a.product_id === productId) ||
              (!a.project_id && !a.product_id))
      ) || [],
    [assetsData?.items, projectId, productId]
  );

  const onFormSubmit = async (values: GenConfigValues) => {
    const seedVal = typeof values.seed === "number" ? values.seed : undefined;
    const base = {
      mode: values.mode,
      steps: values.steps,
      seed: seedVal,
    };

    if (values.mode === "t2v") {
      await onSubmit({ ...base });
    } else if (values.mode === "i2v") {
      await onSubmit({
        ...base,
        first_frame_asset_id: values.first_frame_asset_id,
      });
    } else if (values.mode === "i2v_first_last") {
      await onSubmit({
        ...base,
        first_frame_asset_id: values.first_frame_asset_id,
        last_frame_asset_id: values.last_frame_asset_id,
      });
    } else if (values.mode === "r2v") {
      await onSubmit({
        ...base,
        reference_image_asset_ids: values.reference_image_asset_ids,
        reference_video_asset_ids: values.reference_video_asset_ids,
        reference_audio_asset_ids: values.reference_audio_asset_ids,
      });
    }
  };

  const toggleArrayItem = (fieldName: "reference_image_asset_ids" | "reference_video_asset_ids" | "reference_audio_asset_ids", currentArr: string[], id: string) => {
    if (currentArr.includes(id)) {
      setValue(fieldName, currentArr.filter((item) => item !== id));
    } else {
      setValue(fieldName, [...currentArr, id]);
    }
  };

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title="Cấu hình sinh video (H3 Generation)"
      description="Chọn workflow và tham số thực thi cho scene AI."
      maxWidth="md"
    >
      <form onSubmit={handleSubmit(onFormSubmit)} className="space-y-4">
        <div className="grid gap-1.5">
          <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
            Workflow Mode:
          </label>
          <select
            {...register("mode")}
            className="rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
          >
            <option value="t2v">Text-to-Video (T2V) — Sinh từ prompt thuần túy</option>
            <option value="i2v">Image-to-Video (I2V) — Sinh từ hình ảnh sản phẩm</option>
            <option value="i2v_first_last">First + Last Frame — Nối 2 khung hình</option>
            <option value="r2v">Reference-to-Video (Ref2V) — Giữ dáng &amp; bối cảnh</option>
          </select>
        </div>

        {/* Mode-specific Asset Selectors */}
        {selectedMode === "t2v" && (
          <div className="p-3 rounded-md bg-[#0b101a] border border-[#2c3038] text-xs text-[#9ea5b0]">
            T2V (Text-to-Video): Không yêu cầu tài nguyên khung hình hay reference.
          </div>
        )}

        {selectedMode === "i2v" && (
          <div className="space-y-2 p-3 rounded-md bg-[#0b101a] border border-[#2c3038]">
            <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider block">
              Hình ảnh khung hình đầu (First Frame) *:
            </label>
            {assetsLoading ? (
              <Skeleton className="h-9 w-full" />
            ) : readyImageAssets.length === 0 ? (
              <p className="text-xs text-amber-400">
                Chưa có hình ảnh READY nào trong hệ thống. Vui lòng tải ảnh lên trang Assets trước.
              </p>
            ) : (
              <select
                {...register("first_frame_asset_id")}
                className="w-full rounded-md border border-[#2c3038] bg-[#181a1e] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
              >
                <option value="">-- Chọn hình ảnh sản phẩm --</option>
                {readyImageAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename} ({asset.width}x{asset.height})
                  </option>
                ))}
              </select>
            )}
            {errors.first_frame_asset_id && (
              <p className="text-xs text-red-400">{errors.first_frame_asset_id.message}</p>
            )}
          </div>
        )}

        {selectedMode === "i2v_first_last" && (
          <div className="space-y-3 p-3 rounded-md bg-[#0b101a] border border-[#2c3038]">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider block">
                Khung hình đầu (First Frame) *:
              </label>
              <select
                {...register("first_frame_asset_id")}
                className="w-full rounded-md border border-[#2c3038] bg-[#181a1e] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
              >
                <option value="">-- Chọn ảnh đầu --</option>
                {readyImageAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename} ({asset.width}x{asset.height})
                  </option>
                ))}
              </select>
              {errors.first_frame_asset_id && (
                <p className="text-xs text-red-400">{errors.first_frame_asset_id.message}</p>
              )}
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider block">
                Khung hình cuối (Last Frame) *:
              </label>
              <select
                {...register("last_frame_asset_id")}
                className="w-full rounded-md border border-[#2c3038] bg-[#181a1e] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
              >
                <option value="">-- Chọn ảnh cuối --</option>
                {readyImageAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.filename} ({asset.width}x{asset.height})
                  </option>
                ))}
              </select>
              {errors.last_frame_asset_id && (
                <p className="text-xs text-red-400">{errors.last_frame_asset_id.message}</p>
              )}
            </div>
          </div>
        )}

        {selectedMode === "r2v" && (
          <div className="space-y-3 p-3 rounded-md bg-[#0b101a] border border-[#2c3038] text-xs">
            <span className="font-semibold text-[#9ea5b0] uppercase tracking-wider block">
              Reference Assets (Chọn ít nhất 1 asset):
            </span>

            {/* Images up to 9 */}
            <div>
              <span className="text-[#9ea5b0] block mb-1">
                Reference Images (tối đa 9, đã chọn {selectedRefImages.length}):
              </span>
              <div className="max-h-28 overflow-y-auto space-y-1 p-2 rounded-lg bg-[#181a1e] border border-[#2c3038]">
                {readyImageAssets.length === 0 ? (
                  <span className="text-[#9ea5b0] italic">Không có ảnh READY</span>
                ) : (
                  readyImageAssets.map((asset) => (
                    <label key={asset.id} className="flex items-center gap-2 cursor-pointer hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedRefImages.includes(asset.id)}
                        onChange={() => toggleArrayItem("reference_image_asset_ids", selectedRefImages, asset.id)}
                        className="rounded border-[#2c3038]"
                      />
                      <span className="truncate">{asset.filename}</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            {/* Videos up to 3 */}
            <div>
              <span className="text-[#9ea5b0] block mb-1">
                Reference Videos (tối đa 3, đã chọn {selectedRefVideos.length}):
              </span>
              <div className="max-h-24 overflow-y-auto space-y-1 p-2 rounded-lg bg-[#181a1e] border border-[#2c3038]">
                {readyVideoAssets.length === 0 ? (
                  <span className="text-[#9ea5b0] italic">Không có video READY</span>
                ) : (
                  readyVideoAssets.map((asset) => (
                    <label key={asset.id} className="flex items-center gap-2 cursor-pointer hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedRefVideos.includes(asset.id)}
                        onChange={() => toggleArrayItem("reference_video_asset_ids", selectedRefVideos, asset.id)}
                        className="rounded border-[#2c3038]"
                      />
                      <span className="truncate">{asset.filename}</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            {/* Audio up to 3 */}
            <div>
              <span className="text-[#9ea5b0] block mb-1">
                Reference Audio (tối đa 3, đã chọn {selectedRefAudio.length}):
              </span>
              <div className="max-h-24 overflow-y-auto space-y-1 p-2 rounded-lg bg-[#181a1e] border border-[#2c3038]">
                {readyAudioAssets.length === 0 ? (
                  <span className="text-[#9ea5b0] italic">Không có audio READY</span>
                ) : (
                  readyAudioAssets.map((asset) => (
                    <label key={asset.id} className="flex items-center gap-2 cursor-pointer hover:text-white">
                      <input
                        type="checkbox"
                        checked={selectedRefAudio.includes(asset.id)}
                        onChange={() => toggleArrayItem("reference_audio_asset_ids", selectedRefAudio, asset.id)}
                        className="rounded border-[#2c3038]"
                      />
                      <span className="truncate">{asset.filename}</span>
                    </label>
                  ))
                )}
              </div>
            </div>

            {errors.reference_image_asset_ids && (
              <p className="text-red-400">{errors.reference_image_asset_ids.message}</p>
            )}
          </div>
        )}

        {/* Steps & Seed */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Input
              id="steps"
              label="Inference Steps"
              type="number"
              min={1}
              max={100}
              {...register("steps")}
            />
            {errors.steps && <p className="text-xs text-red-400 mt-1">{errors.steps.message}</p>}
          </div>

          <div>
            <Input
              id="seed"
              label="Seed (Tùy chọn)"
              type="number"
              placeholder="Random"
              {...register("seed")}
            />
            {errors.seed && <p className="text-xs text-red-400 mt-1">{errors.seed.message}</p>}
          </div>
        </div>

        <div className="p-3 rounded-md bg-[#0b101a] border border-[#2c3038] text-xs text-[#9ea5b0]">
          Khung hình: <strong className="text-[#f1f3f5]">{aspectRatio}</strong> · Idempotency-Key được quản lý ở boundary UI để ngăn chặn tạo job trùng lặp khi retry.
        </div>

        <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
          <Button type="button" variant="outline" onClick={onClose}>
            Hủy
          </Button>
          <Button type="submit" variant="primary" isLoading={isLoading}>
            Bắt đầu Dispatch
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// Subcomponent: Edit Scene Dialog (RHF + Zod)
const editSceneSchema = z.object({
  title: z.string().max(255).optional(),
  purpose: z.string().max(80).default("PRODUCT_DETAIL"),
  prompt: z.string().min(1, "Prompt không được để trống").max(20000),
  negative_prompt: z.string().max(10000).optional(),
  duration_seconds: z.coerce.number().min(4, "Thời lượng từ 4 đến 15 giây").max(15, "Thời lượng từ 4 đến 15 giây"),
});

type EditSceneValues = z.infer<typeof editSceneSchema>;

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
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<EditSceneValues>({
    resolver: zodResolver(editSceneSchema),
    defaultValues: {
      title: scene.spec?.title || "",
      purpose: scene.spec?.purpose || "PRODUCT_DETAIL",
      prompt: scene.prompt,
      negative_prompt: scene.negative_prompt,
      duration_seconds: scene.duration_seconds,
    },
  });

  useEffect(() => {
    reset({
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
    reset,
  ]);

  const mutation = useMutation({
    mutationFn: (values: EditSceneValues) =>
      patchScene(
        scene.id,
        {
          prompt: values.prompt,
          negative_prompt: values.negative_prompt,
          duration_seconds: values.duration_seconds,
          spec: {
            ...scene.spec,
            title: values.title,
            purpose: values.purpose,
          },
        },
        scene.revision
      ),
    onSuccess,
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title="Chỉnh sửa Scene"
      description={`Cập nhật kịch bản phân cảnh (Revision #${scene.revision})`}
      maxWidth="md"
    >
      {serverError && (
        <Alert variant="destructive" title="Lỗi cập nhật">
          {serverError}
        </Alert>
      )}

      <form onSubmit={handleSubmit((data) => mutation.mutate(data))} className="space-y-4">
        <div>
          <Input id="scene_title" label="Tên phân cảnh" {...register("title")} />
          {errors.title && <p className="text-xs text-red-400 mt-1">{errors.title.message}</p>}
        </div>

        <div>
          <Textarea id="scene_prompt" label="Prompt hành động & góc quay *" {...register("prompt")} />
          {errors.prompt && <p className="text-xs text-red-400 mt-1">{errors.prompt.message}</p>}
        </div>

        <div>
          <Input id="neg_prompt" label="Negative Prompt" {...register("negative_prompt")} />
          {errors.negative_prompt && <p className="text-xs text-red-400 mt-1">{errors.negative_prompt.message}</p>}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Input
              id="scene_duration"
              label="Thời lượng (4 - 15 giây) *"
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
            <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
              Mục đích scene:
            </label>
            <select
              {...register("purpose")}
              className="rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
            >
              <option value="HOOK">HOOK (Mở đầu cuốn hút)</option>
              <option value="PRODUCT_DETAIL">PRODUCT_DETAIL (Cận cảnh sản phẩm)</option>
              <option value="BENEFIT">BENEFIT (Lợi ích sản phẩm)</option>
              <option value="LIFESTYLE">LIFESTYLE (Đời sống & Trải nghiệm)</option>
              <option value="CTA">CTA (Kêu gọi hành động)</option>
            </select>
          </div>
        </div>

        <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
          <Button type="button" variant="outline" onClick={onClose}>
            Hủy
          </Button>
          <Button type="submit" variant="primary" isLoading={mutation.isPending}>
            Lưu thay đổi
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

// Subcomponent: Add Scene Dialog (RHF + Zod)
const addSceneSchema = z.object({
  title: z.string().max(255).optional(),
  purpose: z.string().max(80).default("PRODUCT_DETAIL"),
  prompt: z.string().min(1, "Prompt không được để trống").max(20000),
  negative_prompt: z.string().max(10000).optional(),
  duration_seconds: z.coerce.number().min(4, "Thời lượng từ 4 đến 15 giây").max(15, "Thời lượng từ 4 đến 15 giây").default(5),
});

type AddSceneValues = z.infer<typeof addSceneSchema>;

function AddSceneDialog({
  isOpen,
  onClose,
  videoId,
  videoRevision,
  onSuccess,
}: {
  isOpen: boolean;
  onClose: () => void;
  videoId: string;
  videoRevision: number;
  onSuccess: () => void;
}) {
  const [serverError, setServerError] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    reset,
    formState: { errors },
  } = useForm<AddSceneValues>({
    resolver: zodResolver(addSceneSchema),
    defaultValues: {
      title: "",
      purpose: "PRODUCT_DETAIL",
      prompt: "",
      negative_prompt: "",
      duration_seconds: 5,
    },
  });

  const mutation = useMutation({
    mutationFn: (values: AddSceneValues) =>
      createScene(
        videoId,
        {
          prompt: values.prompt,
          negative_prompt: values.negative_prompt,
          duration_seconds: values.duration_seconds,
          spec: {
            title: values.title || "Phân cảnh mới",
            purpose: values.purpose,
            description: values.prompt,
            subject: "",
            action: "",
            environment: "",
            camera: "cinematic",
            lighting: "clean",
            style: "commercial",
            continuity: "CUT",
          },
        },
        videoRevision
      ),
    onSuccess: () => {
      reset();
      onSuccess();
    },
    onError: (err) => setServerError(getErrorMessage(err)),
  });

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title="Thêm Phân cảnh Mới"
      description="Tạo thêm phân cảnh thủ công cho video dài."
      maxWidth="md"
    >
      {serverError && (
        <Alert variant="destructive" title="Lỗi thêm scene">
          {serverError}
        </Alert>
      )}

      <form onSubmit={handleSubmit((data) => mutation.mutate(data))} className="space-y-4">
        <div>
          <Input id="add_scene_title" label="Tên phân cảnh" placeholder="Cận cảnh tính năng..." {...register("title")} />
          {errors.title && <p className="text-xs text-red-400 mt-1">{errors.title.message}</p>}
        </div>

        <div>
          <Textarea
            id="add_scene_prompt"
            label="Prompt hành động & góc quay *"
            placeholder="Góc quay điện ảnh 4k cận cảnh sản phẩm..."
            {...register("prompt")}
          />
          {errors.prompt && <p className="text-xs text-red-400 mt-1">{errors.prompt.message}</p>}
        </div>

        <div>
          <Input id="add_neg_prompt" label="Negative Prompt" placeholder="blurry, distorted..." {...register("negative_prompt")} />
          {errors.negative_prompt && <p className="text-xs text-red-400 mt-1">{errors.negative_prompt.message}</p>}
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <Input
              id="add_scene_duration"
              label="Thời lượng (4 - 15 giây) *"
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
            <label className="text-xs font-semibold text-[#9ea5b0] uppercase tracking-wider">
              Mục đích scene:
            </label>
            <select
              {...register("purpose")}
              className="rounded-md border border-[#2c3038] bg-[#0b101a] text-sm text-[#f1f3f5] px-3.5 py-2.5 focus:outline-none focus:border-blue-500"
            >
              <option value="HOOK">HOOK (Mở đầu cuốn hút)</option>
              <option value="PRODUCT_DETAIL">PRODUCT_DETAIL (Cận cảnh sản phẩm)</option>
              <option value="BENEFIT">BENEFIT (Lợi ích sản phẩm)</option>
              <option value="LIFESTYLE">LIFESTYLE (Đời sống & Trải nghiệm)</option>
              <option value="CTA">CTA (Kêu gọi hành động)</option>
            </select>
          </div>
        </div>

        <div className="flex justify-end gap-3 pt-4 border-t border-[#2c3038]">
          <Button type="button" variant="outline" onClick={onClose}>
            Hủy
          </Button>
          <Button type="submit" variant="primary" isLoading={mutation.isPending}>
            Thêm phân cảnh
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
