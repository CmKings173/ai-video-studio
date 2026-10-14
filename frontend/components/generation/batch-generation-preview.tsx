"use client";

import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { formatResolvedResolution } from "@/lib/generation/resolution-labels";
import type { BatchGenerationPreviewDTO, ResolvedGenerationConfiguration } from "@/lib/api/types";

interface BatchGenerationPreviewProps {
  isOpen: boolean;
  onClose: () => void;
  preview: BatchGenerationPreviewDTO | null;
  sceneLabels?: Record<string, string>;
  isLoading: boolean;
  isRevalidating?: boolean;
  isConfirming: boolean;
  error?: string | null;
  notice?: string | null;
  isStale: boolean;
  onPreviewAgain: () => void;
  onConfirm: () => void;
}

function valueOrUnknown(value: string | number | null | undefined, unknown: string): string {
  return value === null || value === undefined || value === "" ? unknown : String(value);
}

function yesNoOrUnknown(value: boolean | null | undefined, yes: string, no: string, unknown: string): string {
  return value === null || value === undefined ? unknown : value ? yes : no;
}

function ConfigurationDetails({ configuration, workflowId, requestedWorkflowId }: {
  configuration: ResolvedGenerationConfiguration;
  workflowId: string;
  requestedWorkflowId: string | null;
}) {
  const { t } = useI18n();
  const unknown = t("Không rõ", "Unknown");
  const seed = configuration.seed_policy === "FIXED"
    ? `${t("Cố định", "Fixed")}: ${valueOrUnknown(configuration.fixed_seed_or_null, unknown)}`
    : configuration.seed_policy === "RANDOM"
      ? t("Ngẫu nhiên", "Random")
      : unknown;
  const items: Array<[string, string]> = [
    [t("Chế độ", "Mode"), `${valueOrUnknown(configuration.requested_mode, unknown)} → ${valueOrUnknown(configuration.resolved_mode, unknown)}`],
    [t("Workflow được yêu cầu", "Requested workflow"), requestedWorkflowId
      ? requestedWorkflowId
      : t("Tự động chọn workflow phù hợp", "Automatic compatible workflow")],
    [t("Workflow đã phân giải", "Resolved workflow"), [
      `${t("Tên", "Name")}: ${valueOrUnknown(configuration.workflow_code, unknown)}`,
      `${t("Phiên bản", "Version")}: ${valueOrUnknown(configuration.workflow_version, unknown)}`,
      `ID: ${valueOrUnknown(workflowId, unknown)}`,
      `${t("Hash", "Hash")}: ${valueOrUnknown(configuration.workflow_hash, unknown)}`,
    ].join(" · ")],
    [t("Chất lượng", "Quality"), [
      `${t("Nhãn", "Label")}: ${valueOrUnknown(configuration.quality_label, unknown)}`,
      `${t("Hồ sơ", "Profile")}: ${valueOrUnknown(configuration.quality_profile, unknown)}`,
    ].join(" · ")],
    [t("Độ phân giải gốc", "Native resolution"), formatResolvedResolution(configuration, "native") ?? unknown],
    [t("Kích thước đầu ra dự kiến", "Expected final resolution"), formatResolvedResolution(configuration, "output") ?? unknown],
    [t("Bước lấy mẫu / CFG", "Sampling steps / CFG"), `${valueOrUnknown(configuration.sampling_steps, unknown)} / ${valueOrUnknown(configuration.cfg, unknown)}`],
    [t("Sampler / scheduler", "Sampler / scheduler"), `${valueOrUnknown(configuration.sampler, unknown)} / ${valueOrUnknown(configuration.scheduler, unknown)}`],
    [t("Model / runtime", "Model / runtime"), [
      `Model: ${valueOrUnknown(configuration.model_identity, unknown)}`,
      `Runtime: ${valueOrUnknown(configuration.runtime_identity, unknown)}`,
    ].join(" · ")],
    [t("Tốc độ khung hình", "Frame rate"), configuration.fps == null ? unknown : `${configuration.fps} FPS`],
    [t("Số khung / thời lượng", "Frames / duration"), `${valueOrUnknown(configuration.frames, unknown)} ${t("khung", "frames")} · ${valueOrUnknown(configuration.duration_seconds, unknown)} ${t("giây", "s")}`],
    [t("Seed", "Seed"), seed],
    [t("Tinh chỉnh", "Refine"), yesNoOrUnknown(configuration.refine_enabled, t("Có", "Yes"), t("Không", "No"), unknown)],
    [t("Chế độ tinh chỉnh", "Refine mode"), valueOrUnknown(configuration.refine_mode, unknown)],
    [t("Nâng độ phân giải", "Upscale"), yesNoOrUnknown(configuration.upscale_enabled, t("Có", "Yes"), t("Không", "No"), unknown)],
    [t("Trạng thái qualification", "Qualification status"), valueOrUnknown(configuration.qualification_status, unknown)],
  ];

  return (
    <dl className="grid gap-x-5 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-[#9ea5b0]">{label}</dt>
          <dd className="mt-1 break-words text-sm text-[#eef0f4]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function BatchGenerationPreview({
  isOpen,
  onClose,
  preview,
  sceneLabels = {},
  isLoading,
  isRevalidating = false,
  isConfirming,
  error,
  notice,
  isStale,
  onPreviewAgain,
  onConfirm,
}: BatchGenerationPreviewProps) {
  const { t } = useI18n();
  const hasScenes = Boolean(preview?.scenes.length);
  const allRunnable = Boolean(hasScenes && preview?.scenes.every((scene) => scene.resolved_configuration.runnable));

  return (
    <Dialog
      isOpen={isOpen}
      onClose={onClose}
      title={t("Xem trước cấu hình Tạo tất cả", "Review Generate All configuration")}
      description={isConfirming
        ? t("Đang gửi các cảnh đã xác nhận…", "Submitting the confirmed scenes…")
        : t(
          "Kiểm tra cấu hình máy chủ đã phân giải cho từng cảnh, rồi xác nhận để bắt đầu.",
          "Review the server-resolved configuration for every scene, then confirm to start.",
        )}
      maxWidth="xl"
    >
      <div className="space-y-4" aria-busy={isLoading || isRevalidating || isConfirming}>
        {isLoading && (
          <div role="status" aria-live="polite" className="space-y-3 rounded-md border border-[#2c3038] bg-[#10151e] p-4">
            <p className="text-sm text-[#c1c7d0]">{t("Đang phân giải cấu hình cho từng cảnh…", "Resolving each scene configuration…")}</p>
            {[0, 1, 2].map((item) => <div key={item} className="h-16 animate-pulse rounded-md bg-[#22252b]" />)}
          </div>
        )}

        {isRevalidating && (
          <div role="status" aria-live="polite" className="rounded-md border border-blue-500/30 bg-blue-950/20 p-3 text-sm text-blue-100">
            {t("Đang kiểm tra lại cấu hình và qualification mới nhất trước khi gửi…", "Rechecking the latest configuration and qualification before dispatch…")}
          </div>
        )}

        {error && (
          <div role="alert" className="rounded-md border border-red-500/40 bg-red-950/20 p-4">
            <p className="text-sm text-red-200">{error}</p>
          </div>
        )}

        {notice && (
          <div role="status" aria-live="polite" className="rounded-md border border-blue-500/30 bg-blue-950/20 p-4 text-sm text-blue-100">
            {notice}
          </div>
        )}

        {isStale && (
          <div role="alert" className="rounded-md border border-amber-500/40 bg-amber-950/20 p-4 text-sm text-amber-100">
            {t(
              "Video, cảnh hoặc cấu hình đã thay đổi sau lần xem trước. Hãy xem trước lại; nút xác nhận đang bị khóa.",
              "The video, scenes, or configuration changed after this preview. Review it again; confirmation is disabled.",
            )}
          </div>
        )}

        {!isLoading && !error && !hasScenes && (
          <div role="status" className="rounded-md border border-[#2c3038] bg-[#10151e] p-6 text-center">
            <h3 className="text-sm font-semibold text-[#eef0f4]">{t("Chưa có cảnh để xem trước", "No scenes to preview")}</h3>
            <p className="mt-1 text-sm text-[#9ea5b0]">
              {t("Hãy cập nhật bảng phân cảnh rồi thử lại.", "Update the storyboard and try again.")}
            </p>
          </div>
        )}

        {preview && hasScenes && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[#2c3038] bg-[#10151e] px-4 py-3">
              <p className="text-sm font-medium text-[#eef0f4]">
                {t(`${preview.scenes.length} cảnh đã được phân giải`, `${preview.scenes.length} scenes resolved`)}
              </p>
              <p className="break-all text-xs text-[#9ea5b0]">
                {t("Bản video", "Video revision")} #{preview.video_revision}
              </p>
            </div>

            {preview.execution_groups.map((group, groupIndex) => (
              <section key={`${group.execution_scope}-${group.scene_ids.join("-")}`} aria-label={t("Nhóm thực thi", "Execution group")} className="space-y-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-[#2c3038] pb-2">
                  <h3 className="text-sm font-semibold text-[#eef0f4]">
                    {t(`Nhóm ${groupIndex + 1} · ${group.execution_scope === "aggregate" ? "Tổng hợp" : "Từng cảnh"}`, `Group ${groupIndex + 1} · ${group.execution_scope === "aggregate" ? "Aggregate" : "Per scene"}`)}
                  </h3>
                  <span className="text-xs text-[#9ea5b0]">
                    {t(`${group.scene_ids.length} cảnh`, `${group.scene_ids.length} scenes`)}
                  </span>
                </div>

                <div className="grid gap-3">
                  {group.scene_ids.map((sceneId) => {
                    const scene = preview.scenes.find((item) => item.scene_id === sceneId);
                    if (!scene) return null;
                    return (
                      <article key={scene.scene_id} className="space-y-4 rounded-md border border-[#2c3038] bg-[#10151e] p-4">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div className="min-w-0">
                            <h4 className="break-words text-sm font-semibold text-[#eef0f4]">
                              {sceneLabels[scene.scene_id] ?? `${t("Cảnh", "Scene")} ${scene.scene_id.slice(0, 8)}`}
                            </h4>
                            <p className="mt-1 text-xs text-[#9ea5b0]">
                              {t("Thời lượng cảnh", "Scene duration")}: {scene.duration_seconds} {t("giây", "s")} · {t("Bản", "Revision")} #{scene.scene_revision}
                            </p>
                          </div>
                          <span className={scene.resolved_configuration.runnable
                            ? "rounded border border-emerald-500/40 bg-emerald-950/20 px-2 py-1 text-xs text-emerald-200"
                            : "rounded border border-amber-500/40 bg-amber-950/20 px-2 py-1 text-xs text-amber-100"}>
                            {scene.resolved_configuration.runnable
                              ? t("Đủ điều kiện chạy", "Runnable")
                              : t("Chưa thể chạy", "Not runnable")}
                          </span>
                        </div>
                        {scene.resolved_configuration.quality_description && (
                          <p className="text-xs leading-5 text-[#c1c7d0]">{scene.resolved_configuration.quality_description}</p>
                        )}
                        <ConfigurationDetails
                          configuration={scene.resolved_configuration}
                          workflowId={scene.workflow_id}
                          requestedWorkflowId={scene.requested_workflow_id}
                        />
                        {!scene.resolved_configuration.runnable && scene.resolved_configuration.unavailable_reason && (
                          <p role="status" className="text-sm text-amber-100">{scene.resolved_configuration.unavailable_reason}</p>
                        )}
                      </article>
                    );
                  })}
                </div>
              </section>
            ))}
          </div>
        )}

        <div className="flex flex-col-reverse gap-2 border-t border-[#2c3038] pt-4 sm:flex-row sm:justify-between">
          <Button type="button" variant="secondary" onClick={onPreviewAgain} disabled={isLoading || isRevalidating || isConfirming}>
            {t("Cập nhật bản xem trước", "Refresh preview")}
          </Button>
          <div className="flex flex-col-reverse gap-2 sm:flex-row">
            <Button type="button" variant="outline" onClick={onClose} disabled={isConfirming}>
              {t("Đóng", "Close")}
            </Button>
            <Button type="button" onClick={onConfirm} isLoading={isConfirming}
              disabled={isLoading || isRevalidating || isStale || !allRunnable || isConfirming}>
              {isRevalidating
                ? t("Đang kiểm tra lại…", "Rechecking…")
                : t("Xác nhận và tạo", "Confirm and generate")}
            </Button>
          </div>
        </div>
      </div>
    </Dialog>
  );
}
