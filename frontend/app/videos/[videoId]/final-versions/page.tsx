"use client";

import React, { use, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  ArrowLeft,
  XCircle,
  Sparkles,
} from "lucide-react";
import { getVideo } from "@/lib/api/videos";
import { listFinalVersions, downloadFinalVersion, cancelFinalVersion } from "@/lib/api/assembly";
import { queryKeys } from "@/lib/query/query-keys";
import { useVideoEvents } from "@/lib/hooks/use-video-events";
import { getErrorMessage } from "@/lib/api/errors";
import { getProgressPercent } from "@/lib/utils/progress";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { VideoPreview } from "@/components/ui/video-preview";

function configString(config: Record<string, unknown>, key: string, fallback: string): string {
  const value = config[key];
  return typeof value === "string" ? value : fallback;
}

export default function VersionHistoryPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { videoId } = use(params);
  const queryClient = useQueryClient();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const { assemblyProgress } = useVideoEvents(videoId);

  const { data: video } = useQuery({
    queryKey: queryKeys.videos.detail(videoId),
    queryFn: () => getVideo(videoId),
  });

  const { data: versions, isLoading, error } = useQuery({
    queryKey: queryKeys.videos.finalVersions(videoId),
    queryFn: () => listFinalVersions(videoId),
  });

  const cancelMutation = useMutation({
    mutationFn: (finalId: string) => cancelFinalVersion(finalId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.finalVersions(videoId) });
    },
    onError: (err) => alert(getErrorMessage(err, "Không thể hủy assembly")),
  });

  const handleDownload = async (finalId: string) => {
    setDownloadingId(finalId);
    try {
      const download = await downloadFinalVersion(finalId);
      window.open(download.url, "_blank");
    } catch (err) {
      alert(getErrorMessage(err, "Không thể lấy link tải video"));
    } finally {
      setDownloadingId(null);
    }
  };

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
          eyebrow="Lịch sử phiên bản video"
          title={`Phiên bản xuất bản: ${video?.title || "Video"}`}
          description="Final video không overwrite; mỗi lần xuất bản FFmpeg tạo một version mới độc lập với đầy đủ manifest."
        >
          <Link
            href={`/videos/${videoId}/assembly`}
            className="primary-action text-xs flex items-center gap-1.5"
          >
            <Sparkles className="w-4 h-4" />
            <span>Xuất bản Version mới</span>
          </Link>
        </PageHeader>
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title="Lỗi tải phiên bản">
          {getErrorMessage(error)}
        </Alert>
      ) : !versions?.length ? (
        <EmptyState
          title="Chưa có phiên bản hoàn chỉnh nào"
          detail="Lắp ghép các phân cảnh đã duyệt để sinh file MP4 đầu tiên."
          action={{ label: "Chuyển sang Assembly", href: `/videos/${videoId}/assembly` }}
        />
      ) : (
        <section className="space-y-4">
          {versions.map((version) => {
            const isReady = version.status === "READY";
            const isRunning = ["QUEUED", "ASSEMBLING"].includes(version.status);
            const liveProgress = assemblyProgress[version.id];
            const currentPercent = getProgressPercent(
              liveProgress?.progress,
              version.progress_current,
              version.progress_total
            );

            return (
              <div
                key={version.id}
                className="p-5 rounded-md border border-[#2c3038] bg-[#181a1e] hover:border-[#2a2e37] transition-all space-y-4"
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2.5">
                      <span className="font-semibold text-lg text-[#f1f3f5]">
                        Version #{version.version_no}
                      </span>
                      <StatusPill status={version.status} />
                      {version.id === video?.current_final_video_id && (
                        <span className="text-[10px] font-semibold text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded-full border border-blue-500/30">
                          Current Final
                        </span>
                      )}
                    </div>

                    <p className="text-xs text-[#9ea5b0]">
                      Xuất bản: {new Date(version.created_at).toLocaleString("vi-VN")} · Transition:{" "}
                      {configString(version.assembly_config, "transition", "CUT")} · Audio:{" "}
                      {configString(version.assembly_config, "audio_mode", "KEEP")}
                    </p>
                  </div>

                  <div className="flex items-center gap-3">
                    {isRunning && (
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => cancelMutation.mutate(version.id)}
                        isLoading={cancelMutation.isPending}
                      >
                        <XCircle className="w-4 h-4" />
                        <span>Hủy Assembly</span>
                      </Button>
                    )}

                    {isReady && (
                      <Button
                        size="sm"
                        variant="primary"
                        onClick={() => handleDownload(version.id)}
                        isLoading={downloadingId === version.id}
                      >
                        <Download className="w-4 h-4" />
                        <span>Tải file MP4</span>
                      </Button>
                    )}
                  </div>
                </div>

                {/* Real video preview for ready final version */}
                {isReady && version.output_asset_id && (
                  <div className="w-full max-w-2xl pt-2">
                    <VideoPreview assetId={version.output_asset_id} className="max-h-[360px] w-full" />
                  </div>
                )}

                {isRunning && (
                  <div className="pt-2 border-t border-[#2c3038]/60">
                    <Progress
                      value={currentPercent}
                      label={liveProgress?.stage || version.phase || "Đang render video..."}
                    />
                  </div>
                )}

                {version.error_message && (
                  <Alert variant="destructive" title="Lỗi assemble">
                    {version.error_message}
                  </Alert>
                )}
              </div>
            );
          })}
        </section>
      )}
    </div>
  );
}
