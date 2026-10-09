"use client";

import { useI18n } from "@/lib/i18n";
import React, { use, useRef, useState } from "react";
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
import { getErrorMessage, isRevisionConflict } from "@/lib/api/errors";
import { getProgressPercent } from "@/lib/utils/progress";
import { isCurrentFinal } from "@/lib/utils/current-final";
import { PageHeader, StatusPill, EmptyState } from "@/components/page-kit";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Alert } from "@/components/ui/alert";
import { VideoPreview } from "@/components/ui/video-preview";
import { closePresignedDownloadTab, navigatePresignedDownload, reservePresignedDownloadTab } from "@/lib/utils/presigned-download";

function configString(config: Record<string, unknown>, key: string, fallback: string): string {
  const value = config[key];
  return typeof value === "string" ? value : fallback;
}

export default function VersionHistoryPage({
  params,
}: {
  params: Promise<{ videoId: string }>;
}) {
  const { t } = useI18n();
  const transitionLabel = (value: string) => value === "CUT" ? t("Cắt nối", "Cut") : value === "CROSSFADE" ? t("Chồng mờ", "Crossfade") : value;
  const audioLabel = (value: string) => value === "KEEP" || value === "KEEP_SCENE_AUDIO" ? t("Giữ âm thanh cảnh", "Keep scene audio") : value === "MUTE_SCENE_AUDIO" ? t("Tắt tiếng cảnh", "Mute scene audio") : value;
  const uiError = (error: unknown, fallback = t("Đã xảy ra lỗi không xác định", "An unknown error occurred")) =>
    isRevisionConflict(error)
      ? t("Dữ liệu đã thay đổi. Vui lòng tải lại và thử lại.", "The data has changed. Reload and try again.")
      : getErrorMessage(error, fallback);
  const { videoId } = use(params);
  const queryClient = useQueryClient();
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [serverError, setServerError] = useState<string | null>(null);
  const downloadInFlight = useRef(false);

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
    onMutate: () => setServerError(null),
    onSuccess: () => {
      setServerError(null);
      queryClient.invalidateQueries({ queryKey: queryKeys.videos.finalVersions(videoId) });
    },
    onError: (err) => setServerError(uiError(err, t("Không thể hủy ghép video", "Unable to cancel assembly"))),
  });

  const handleDownload = async (finalId: string) => {
    if (downloadInFlight.current) return;
    const tab = reservePresignedDownloadTab();
    if (!tab) {
      setServerError(t("Trình duyệt đã chặn thẻ tải xuống. Cho phép mở thẻ rồi thử lại.", "Your browser blocked the download tab. Allow new tabs and try again."));
      return;
    }
    downloadInFlight.current = true;
    setServerError(null);
    setDownloadingId(finalId);
    try {
      const download = await downloadFinalVersion(finalId);
      navigatePresignedDownload(tab, download.url);
    } catch (err) {
      closePresignedDownloadTab(tab);
      setServerError(uiError(err, t("Không thể lấy liên kết tải video", "Unable to get the video download link")));
    } finally {
      downloadInFlight.current = false;
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
          <span>{t("Quay lại không gian làm việc với bảng phân cảnh", "Back to storyboard workspace")}</span>
        </Link>

        <PageHeader
          eyebrow={t("Lịch sử phiên bản video", "Video version history")}
          title={t(`Các bản xuất: ${video?.title || "Video"}`, `Exported versions: ${video?.title || "Video"}`)}
          description={t("Mỗi lần xuất video tạo một phiên bản MP4 mới, kèm thông tin các cảnh và cấu hình đã dùng. Các phiên bản trước được giữ lại.", "Each export creates a new MP4 version with the scene and configuration details used. Previous versions are retained.")}
        >
          <Link
            href={`/videos/${videoId}/assembly`}
            className="primary-action text-xs flex items-center gap-1.5"
          >
            <Sparkles className="w-4 h-4" />
            <span>{t("Xuất phiên bản mới", "Export new version")}</span>
          </Link>
        </PageHeader>
      </div>

      {serverError && (
        <Alert variant="destructive" title={t("Không thể hoàn tất thao tác", "Unable to complete the action")}>
          {serverError}
        </Alert>
      )}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24" />
          <Skeleton className="h-24" />
        </div>
      ) : error ? (
        <Alert variant="destructive" title={t("Không thể tải phiên bản", "Unable to load versions")}>
          {uiError(error)}
        </Alert>
      ) : !versions?.length ? (
        <EmptyState
          title={t("Chưa có phiên bản hoàn chỉnh nào", "No completed versions yet")}
          detail={t("Ghép các cảnh đã duyệt để tạo tệp MP4 đầu tiên.", "Assemble approved scenes to create your first MP4 file.")}
          action={{ label: t("Ghép & xuất video", "Assemble & export video"), href: `/videos/${videoId}/assembly` }}
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
                        {t(`Phiên bản #${version.version_no}`, `Version #${version.version_no}`)}
                      </span>
                      <StatusPill status={version.status} />
                      {isCurrentFinal(video, version.id) && (
                        <span className="text-[10px] font-semibold text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded-full border border-blue-500/30">
                          {t("Bản cuối hiện tại", "Current final")}</span>
                      )}
                    </div>

                    <p className="text-xs text-[#9ea5b0]">
                      {t(`Xuất lúc: ${new Date(version.created_at).toLocaleString("vi-VN")} · Chuyển cảnh: ${transitionLabel(configString(version.assembly_config, "transition", "CUT"))} · Âm thanh: ${audioLabel(configString(version.assembly_config, "audio_mode", "KEEP"))}`, `Exported: ${new Date(version.created_at).toLocaleString("en-US")} · Transition: ${transitionLabel(configString(version.assembly_config, "transition", "CUT"))} · Audio: ${audioLabel(configString(version.assembly_config, "audio_mode", "KEEP"))}`)}
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
                        <span>{t("Hủy ghép video", "Cancel assembly")}</span>
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
                        <span>{t("Tải tệp MP4", "Download MP4")}</span>
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
                      label={liveProgress?.stage || version.phase || t("Đang kết xuất video...", "Rendering video...")}
                    />
                  </div>
                )}

                {version.error_message && (
                  <Alert variant="destructive" title={t("Lỗi ghép video", "Assembly error")}>
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
