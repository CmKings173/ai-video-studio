"use client";

import React, { useRef, useState, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, AlertCircle, Loader2 } from "lucide-react";
import { downloadAsset } from "@/lib/api/assets";
import { queryKeys } from "@/lib/query/query-keys";

export interface VideoPreviewProps {
  assetId?: string | null;
  className?: string;
  autoPlay?: boolean;
  controls?: boolean;
  loop?: boolean;
  muted?: boolean;
  fallbackMessage?: string;
}

export function VideoPreview({
  assetId,
  className = "",
  autoPlay = false,
  controls = true,
  loop = false,
  muted = false,
  fallbackMessage = "Không thể tải video",
}: VideoPreviewProps) {
  const queryClient = useQueryClient();
  const retryCountRef = useRef(0);
  const [retryKey, setRetryKey] = useState(0);

  // Reset retry count when assetId changes
  useEffect(() => {
    retryCountRef.current = 0;
  }, [assetId]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: queryKeys.assets.download(assetId || ""),
    queryFn: () => downloadAsset(assetId!),
    enabled: !!assetId,
    staleTime: 5 * 60 * 1000, // 5 minutes, safely under 15-minute presigned URL TTL
    gcTime: 10 * 60 * 1000,
  });

  const handleMediaError = useCallback(() => {
    if (retryCountRef.current < 1 && assetId) {
      retryCountRef.current += 1;
      queryClient.invalidateQueries({ queryKey: queryKeys.assets.download(assetId) }).then(() => {
        setRetryKey((k) => k + 1);
      });
    }
  }, [assetId, queryClient]);

  if (!assetId) {
    return (
      <div
        className={`flex flex-col items-center justify-center p-6 rounded-md bg-[#0b101a] border border-[#2c3038] text-center text-[#9ea5b0] ${className}`}
      >
        <Film className="w-8 h-8 mb-2 opacity-50" />
        <span className="text-xs">Chưa có output video</span>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div
        className={`flex flex-col items-center justify-center p-8 rounded-md bg-[#0b101a] border border-[#2c3038] text-[#9ea5b0] animate-pulse ${className}`}
      >
        <Loader2 className="w-8 h-8 animate-spin text-blue-400 mb-2" />
        <span className="text-xs">Đang lấy link video...</span>
      </div>
    );
  }

  if (isError || !data?.url) {
    return (
      <div
        className={`flex flex-col items-center justify-center p-6 rounded-md bg-red-950/20 border border-red-800/30 text-red-400 text-center ${className}`}
      >
        <AlertCircle className="w-6 h-6 mb-2" />
        <span className="text-xs font-semibold">{fallbackMessage}</span>
        <span className="text-[11px] text-red-400/70 mt-1">
          {error instanceof Error ? error.message : "Presigned URL không khả dụng"}
        </span>
      </div>
    );
  }

  return (
    <div className={`relative overflow-hidden rounded-md border border-[#2c3038] bg-black ${className}`}>
      <video
        key={`${data.url}-${retryKey}`}
        controls={controls}
        autoPlay={autoPlay}
        loop={loop}
        muted={muted}
        preload="metadata"
        className="w-full h-full max-h-[480px] object-contain rounded-md"
        onError={handleMediaError}
      >
        <source src={data.url} type="video/mp4" onError={handleMediaError} />
        Trình duyệt không hỗ trợ phát thẻ video HTML5.
      </video>
    </div>
  );
}
