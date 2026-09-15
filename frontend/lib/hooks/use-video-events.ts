"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiUrl } from "../api/client";
import { queryKeys } from "../query/query-keys";

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
  const [isConnected, setIsConnected] = useState(false);
  const [generationProgress, setGenerationProgress] = useState<Record<string, VideoEventProgress>>({});
  const [assemblyProgress, setAssemblyProgress] = useState<Record<string, VideoEventProgress>>({});
  const [sceneActiveGeneration, setSceneActiveGeneration] = useState<Record<string, string>>({});
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!videoId) {
      return;
    }

    const url = apiUrl(`/api/v1/videos/${videoId}/events`);
    const eventSource = new EventSource(url, { withCredentials: true });
    eventSourceRef.current = eventSource;

    eventSource.onopen = () => {
      setIsConnected(true);
    };

    eventSource.onerror = () => {
      setIsConnected(false);
    };

    // Generic event handler
    const handleEvent = (event: MessageEvent, type: string) => {
      try {
        const data = JSON.parse(event.data);

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
            setGenerationProgress((prev) => ({
              ...prev,
              [data.generation_id]: {
                status: data.status,
                stage: data.stage,
                progress: data.progress,
                outputAssetId: data.output_asset_id,
                errorCode: data.error_code,
                errorMessage: data.error_message,
              },
            }));
            queryClient.invalidateQueries({ queryKey: queryKeys.generations.detail(data.generation_id) });
          }
          if (data.generation_id && data.scene_id) {
            setSceneActiveGeneration((prev) => ({
              ...prev,
              [data.scene_id]: data.generation_id,
            }));
          }
          if (data.scene_id) {
            queryClient.invalidateQueries({ queryKey: queryKeys.scenes.generations(data.scene_id) });
          }
          queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(videoId) });
          queryClient.invalidateQueries({ queryKey: queryKeys.dashboard.summary });
        } else if (type.startsWith("assembly.")) {
          if (data.final_video_id) {
            setAssemblyProgress((prev) => ({
              ...prev,
              [data.final_video_id]: {
                status: data.status,
                stage: data.stage,
                progress: data.progress,
                outputAssetId: data.output_asset_id,
                errorCode: data.error_code,
                errorMessage: data.error_message,
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

    return () => {
      listeners.forEach(({ type, listener }) => {
        eventSource.removeEventListener(type, listener);
      });
      eventSource.close();
      eventSourceRef.current = null;
      setIsConnected(false);
    };
  }, [videoId, queryClient]);

  return {
    isConnected,
    generationProgress,
    assemblyProgress,
    sceneActiveGeneration,
  };
}
