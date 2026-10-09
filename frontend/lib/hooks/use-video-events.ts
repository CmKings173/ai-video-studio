"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiUrl } from "../api/client";
import { queryKeys } from "../query/query-keys";

const FALLBACK_POLL_MS = 12_000;
const ACTIVE_STATUSES = new Set([
  "CREATED",
  "DISPATCHING",
  "QUEUED",
  "RUNNING",
  "COLLECTING",
  "CANCEL_REQUESTED",
  "ASSEMBLING",
  "GENERATING",
]);
const TERMINAL_EVENT_STATUS: Record<string, string> = {
  "generation.completed": "COMPLETED",
  "generation.failed": "FAILED",
  "generation.cancelled": "CANCELLED",
  "assembly.completed": "READY",
  "assembly.failed": "FAILED",
};

function isActiveStatus(status: unknown): status is string {
  return typeof status === "string" && ACTIVE_STATUSES.has(status.toUpperCase());
}

function asRecords(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
  if (!value || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  for (const key of ["items", "scenes", "generations", "final_versions", "finalVersions"]) {
    if (Array.isArray(record[key])) return (record[key] as unknown[]).filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
  }
  if (Array.isArray(record.pages)) return record.pages.flatMap(asRecords);
  return [];
}

function collectSceneIds(value: unknown): string[] {
  const ids = new Set<string>();
  for (const record of asRecords(value)) {
    if (typeof record.id === "string") ids.add(record.id);
    if (typeof record.scene_id === "string") ids.add(record.scene_id);
  }
  return [...ids];
}

function collectStatuses(value: unknown, result: Map<string, string>, visited = new Set<object>(), depth = 0): void {
  if (!value || typeof value !== "object" || visited.has(value as object) || depth > 8) return;
  visited.add(value as object);
  if (Array.isArray(value)) {
    for (const item of value) collectStatuses(item, result, visited, depth + 1);
    return;
  }
  const record = value as Record<string, unknown>;
  if (typeof record.id === "string" && typeof record.status === "string") result.set(record.id, record.status);
  for (const [key, nested] of Object.entries(record)) {
    if (["items", "pages", "scenes", "generations", "final_versions", "finalVersions", "generation", "final_video"].includes(key)) {
      collectStatuses(nested, result, visited, depth + 1);
    }
  }
}

function inferEventStatus(type: string, data: Record<string, unknown>): string | undefined {
  if (typeof data.status === "string") return data.status;
  if (TERMINAL_EVENT_STATUS[type]) return TERMINAL_EVENT_STATUS[type];
  if (type.endsWith(".queued")) return "QUEUED";
  if (type.endsWith(".started")) return type.startsWith("assembly.") ? "ASSEMBLING" : "RUNNING";
  if (type.endsWith(".progress")) return type.startsWith("assembly.") ? "ASSEMBLING" : "RUNNING";
  return undefined;
}

export interface VideoEventProgress {
  status: string;
  stage?: string;
  progress?: number;
  outputAssetId?: string;
  errorCode?: string;
  errorMessage?: string;
}

export function useVideoEvents(videoId: string | undefined | null) {
  const queryClient = useQueryClient();
  const scopeId = videoId ?? null;
  const [connectionState, setConnectionState] = useState({ videoId: scopeId, connected: false });
  const [generationState, setGenerationState] = useState<{ videoId: string | null; value: Record<string, VideoEventProgress> }>({ videoId: scopeId, value: {} });
  const [assemblyState, setAssemblyState] = useState<{ videoId: string | null; value: Record<string, VideoEventProgress> }>({ videoId: scopeId, value: {} });
  const [sceneGenerationState, setSceneGenerationState] = useState<{ videoId: string | null; value: Record<string, string> }>({ videoId: scopeId, value: {} });
  const isConnected = connectionState.videoId === scopeId && connectionState.connected;
  const generationProgress = generationState.videoId === scopeId ? generationState.value : {};
  const assemblyProgress = assemblyState.videoId === scopeId ? assemblyState.value : {};
  const sceneActiveGeneration = sceneGenerationState.videoId === scopeId ? sceneGenerationState.value : {};

  useEffect(() => {
    if (!videoId) {
      return;
    }

    let disposed = false;
    let connected = false;
    let fallbackTimer: ReturnType<typeof setInterval> | null = null;
    let syncInFlight: Promise<void> | null = null;
    const activeWork = new Map<string, string>();

    const isVisible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
    const stopFallback = () => {
      if (fallbackTimer !== null) {
        clearInterval(fallbackTimer);
        fallbackTimer = null;
      }
    };

    const relatedCacheQueries = () => {
      const cache = queryClient.getQueryCache();
      const all = cache.getAll();
      const sceneQuery = all.find((query) => JSON.stringify(query.queryKey) === JSON.stringify(queryKeys.videos.scenes(videoId)));
      const videoQuery = all.find((query) => JSON.stringify(query.queryKey) === JSON.stringify(queryKeys.videos.detail(videoId)));
      const finalQuery = all.find((query) => JSON.stringify(query.queryKey) === JSON.stringify(queryKeys.videos.finalVersions(videoId)));
      const sceneIds = new Set([
        ...collectSceneIds(sceneQuery?.state.data),
        ...collectSceneIds((videoQuery?.state.data as { scenes?: unknown } | undefined)?.scenes),
      ]);
      const finalIds = new Set(collectSceneIds(finalQuery?.state.data));
      const relevant = all.filter((query) => {
        const key = query.queryKey as unknown[];
        if (JSON.stringify(key) === JSON.stringify(queryKeys.videos.detail(videoId))) return true;
        if (JSON.stringify(key) === JSON.stringify(queryKeys.videos.scenes(videoId))) return true;
        if (JSON.stringify(key) === JSON.stringify(queryKeys.videos.finalVersions(videoId))) return true;
        if (key[0] === "scenes" && typeof key[1] === "string" && key[2] === "generations" && sceneIds.has(key[1])) return true;
        if (key[0] === "generations" && key[1] === "detail" && typeof key[2] === "string" && activeWork.has(key[2])) return true;
        if (key[0] === "final-versions" && key[1] === "detail" && typeof key[2] === "string" && finalIds.has(key[2])) return true;
        return false;
      });
      return { all, sceneIds: [...sceneIds], finalIds: [...finalIds], relevant };
    };

    const reconcileCachedStatuses = () => {
      const { relevant } = relatedCacheQueries();
      const statuses = new Map<string, string>();
      for (const query of relevant) collectStatuses(query.state.data, statuses);
      for (const [id, status] of statuses) {
        if (isActiveStatus(status)) activeWork.set(id, status);
        else activeWork.delete(id);
      }
      return activeWork.size > 0;
    };

    const syncVideoState = (): Promise<void> => {
      if (syncInFlight) return syncInFlight;
      const keys = relatedCacheQueries();
      const requests = [
        queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.videos.scenes(videoId) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.videos.finalVersions(videoId) }),
        ...keys.sceneIds.map((sceneId) => queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(sceneId) })),
        ...keys.sceneIds.map((sceneId) => queryClient.invalidateQueries({ queryKey: queryKeys.scenes.detail(sceneId) })),
        ...keys.finalIds.map((id) => queryClient.invalidateQueries({ queryKey: queryKeys.finalVersions.detail(id) })),
        ...[...activeWork.keys()].map((id) => queryClient.invalidateQueries({ queryKey: queryKeys.generations.detail(id) })),
      ];
      syncInFlight = Promise.all(requests).then(() => {
        if (disposed) return;
        reconcileCachedStatuses();
        // Cached server state is authoritative after a reconnect/resync; SSE progress may have missed a terminal event.
        setGenerationState({ videoId, value: {} });
        setAssemblyState({ videoId, value: {} });
        setSceneGenerationState({ videoId, value: {} });
      }).finally(() => { syncInFlight = null; });
      return syncInFlight;
    };

    const ensureFallbackTimer = () => {
      if (disposed || connected || !isVisible() || fallbackTimer !== null || activeWork.size === 0) return;
      fallbackTimer = setInterval(() => {
        if (!isVisible()) return;
        return syncVideoState().then(() => {
          if (disposed || connected) return;
          if (!reconcileCachedStatuses()) stopFallback();
        });
      }, FALLBACK_POLL_MS);
    };

    const handleOffline = () => {
      connected = false;
      setConnectionState({ videoId, connected: false });
      void syncVideoState().then(() => {
        if (disposed || connected) return;
        if (reconcileCachedStatuses()) ensureFallbackTimer();
        else stopFallback();
      });
    };

    const url = apiUrl(`/api/v1/videos/${videoId}/events`);
    const eventSource = new EventSource(url, { withCredentials: true });

    eventSource.onopen = () => {
      connected = true;
      stopFallback();
      setConnectionState({ videoId, connected: true });
      void syncVideoState();
    };

    eventSource.onerror = () => {
      handleOffline();
    };

    // Generic event handler
    const handleEvent = (event: MessageEvent, type: string) => {
      try {
        const data = JSON.parse(event.data);
        const status = inferEventStatus(type, data);
        const workId = type.startsWith("generation.") ? data.generation_id : data.final_video_id;
        if (typeof workId === "string" && status) {
          if (isActiveStatus(status)) activeWork.set(workId, status);
          else activeWork.delete(workId);
        }

        if (type.startsWith("video.")) {
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.all });
        } else if (type.startsWith("scene.")) {
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.scenes(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          if (data.scene_id) {
            queryClient.invalidateQueries({ queryKey: queryKeys.scenes.detail(data.scene_id) });
          }
        } else if (type.startsWith("generation.")) {
          if (data.generation_id) {
            setGenerationState((prev) => ({
              videoId,
              value: {
                ...(prev.videoId === videoId ? prev.value : {}),
                [data.generation_id]: {
                  status: status ?? "UNKNOWN",
                  stage: data.stage,
                  progress: data.progress,
                  outputAssetId: data.output_asset_id,
                  errorCode: data.error_code,
                  errorMessage: data.error_message,
                },
              },
            }));
            queryClient.invalidateQueries({ queryKey: queryKeys.generations.detail(data.generation_id) });
          }
          if (data.generation_id && data.scene_id) {
            setSceneGenerationState((prev) => ({
              videoId,
              value: {
                ...(prev.videoId === videoId ? prev.value : {}),
                [data.scene_id]: data.generation_id,
              },
            }));
          }
          if (data.scene_id) {
            queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(data.scene_id) });
          }
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
        } else if (type.startsWith("assembly.")) {
          if (data.final_video_id) {
            setAssemblyState((prev) => ({
              videoId,
              value: {
                ...(prev.videoId === videoId ? prev.value : {}),
                [data.final_video_id]: {
                  status: status ?? "UNKNOWN",
                  stage: data.stage,
                  progress: data.progress,
                  outputAssetId: data.output_asset_id,
                  errorCode: data.error_code,
                  errorMessage: data.error_message,
                },
              },
            }));
          }
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.finalVersions(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
        }
      } catch (err) {
        console.error("Failed to parse SSE event payload", err);
      }
    };

    const eventTypes = [
      "video.updated",
      "scene.updated",
      "generation.queued",
      "generation.started",
      "generation.progress",
      "generation.completed",
      "generation.failed",
      "generation.cancelled",
      "assembly.started",
      "assembly.progress",
      "assembly.completed",
      "assembly.failed",
    ];

    const listeners: { type: string; listener: (e: MessageEvent) => void }[] = [];

    eventTypes.forEach((type) => {
      const listener = (e: MessageEvent) => handleEvent(e, type);
      listeners.push({ type, listener });
      eventSource.addEventListener(type, listener);
    });

    const unsubscribeQueryCache = queryClient.getQueryCache().subscribe(() => {
      if (!connected && isVisible() && reconcileCachedStatuses()) ensureFallbackTimer();
      else if (!connected && activeWork.size === 0) stopFallback();
    });
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        stopFallback();
      } else if (!connected) {
        handleOffline();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      disposed = true;
      stopFallback();
      unsubscribeQueryCache();
      document.removeEventListener("visibilitychange", onVisibilityChange);
      listeners.forEach(({ type, listener }) => {
        eventSource.removeEventListener(type, listener);
      });
      eventSource.close();
      activeWork.clear();
    };
  }, [videoId, queryClient]);

  return {
    isConnected,
    generationProgress,
    assemblyProgress,
    sceneActiveGeneration,
  };
}
