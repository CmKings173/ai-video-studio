"use client";

import { useQuery } from "@tanstack/react-query";
import { downloadAsset } from "@/lib/api/assets";
import { generationCapabilities } from "@/lib/api/generations";
import type { GenerationDTO, SceneDTO } from "@/lib/api/types";
import { queryKeys } from "@/lib/query/query-keys";
import { getErrorMessage } from "@/lib/api/errors";
import { qualifiedCombinations } from "@/lib/generation/capabilities";
import { historyGenerationRequiresAggregate } from "@/lib/generation/eligible-scenes";
import { aggregateGuidance } from "@/lib/generation/workspace-state";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";

export function GenerationHistoryDetails({ generation, scene, scenes, onRegenerate, onVariation, onReuse, busy }: {
  scene: SceneDTO | undefined; scenes: SceneDTO[];
  generation: GenerationDTO; onRegenerate: () => void; onVariation: () => void; onReuse: () => void; busy: boolean;
}) {
  const { t } = useI18n();
  const shown = (value: unknown): string => typeof value === "string" || typeof value === "number" ? String(value) : t("Chưa có dữ liệu", "No data");
  const qualityLabel = (value: unknown) => typeof value === "string" ? ({ DRAFT: t("Bản nháp", "Draft"), STANDARD: t("Tiêu chuẩn", "Standard"), HIGH: t("Cao", "High"), BASE: t("Cơ bản", "Base"), HD: t("HD", "HD"), FULL_HD_REFINED: t("Full HD đã tinh chỉnh", "Refined Full HD"), CUSTOM: t("Tùy chỉnh", "Custom") } as Record<string, string>)[value] ?? shown(value) : shown(value);
  const snapshot = generation.input_snapshot;
  const requiresAggregate = !!scene && historyGenerationRequiresAggregate(scene, scenes, generation);
  const director = snapshot.director_execution_spec;
  const frozenDirector = snapshot.director_execution;
  const frozenGeneration = frozenDirector ? {
    width: frozenDirector.canvas.width, height: frozenDirector.canvas.height,
    fps: frozenDirector.fps, frame_count: frozenDirector.frames, seed: frozenDirector.seed,
    duration_seconds: frozenDirector.resolved_duration_seconds,
  } : director?.generation;
  const measured = generation.output_metadata ?? {};
  const caps = useQuery({ queryKey: ["generation-capabilities"], queryFn: generationCapabilities });
  const parentAvailable = caps.data?.available && qualifiedCombinations(caps.data).some((item) => item.workflow_id === generation.workflow_id
    && (item.mode === generation.mode || (item.mode === "fl2v" && ["i2v_last", "i2v_first_last"].includes(generation.mode)))
    && item.execution_scope === "single_scene" && item.quality_profile === (snapshot.requested_quality_profile ?? "STANDARD")
    && (!snapshot.requested_aspect_ratio || item.aspect_ratio === snapshot.requested_aspect_ratio));
  const { data: download, error } = useQuery({ queryKey: queryKeys.assets.download(generation.output_asset_id ?? ""),
    queryFn: () => downloadAsset(generation.output_asset_id!), enabled: generation.status === "COMPLETED" && !!generation.output_asset_id });

  return <div className="space-y-3">
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
      <dt className="text-[#9ea5b0]">{t("Thao tác / chất lượng", "Operation / quality")}</dt><dd>{generation.operation === "ORIGINAL" ? t("Tạo mới", "Original") : generation.operation === "REGENERATE" ? t("Tạo lại", "Regenerate") : t("Biến thể", "Variation")} · {qualityLabel(snapshot.requested_quality_profile)}</dd>
      <dt className="text-[#9ea5b0]">{t("Tác vụ", "Task")}</dt><dd>{shown(frozenDirector?.task ?? snapshot.task ?? director?.intent?.director_task ?? generation.mode)}</dd>
      <dt className="text-[#9ea5b0]">{t("Seed", "Seed")}</dt><dd>{shown(snapshot.seed ?? frozenGeneration?.seed)}</dd>
      {measured.director_run_id != null && <>
        <dt className="text-[#9ea5b0]">{t("Lần chạy Director", "Director run")}</dt><dd>{shown(measured.director_run_id)}</dd>
        <dt className="text-[#9ea5b0]">{t("Seed chung của nhóm", "Aggregate global seed")}</dt><dd>{shown(measured.resolved_global_seed)}</dd>
      </>}
      <dt className="text-[#9ea5b0]">{t("Kích thước yêu cầu", "Requested dimensions")}</dt><dd>{shown(snapshot.requested_width ?? frozenGeneration?.width ?? snapshot.width)} x {shown(snapshot.requested_height ?? frozenGeneration?.height ?? snapshot.height)}</dd>
      <dt className="text-[#9ea5b0]">{t("Kích thước được xác định", "Resolved dimensions")}</dt><dd>{shown(snapshot.resolved_width ?? frozenGeneration?.width ?? snapshot.width)} x {shown(snapshot.resolved_height ?? frozenGeneration?.height ?? snapshot.height)}</dd>
      <dt className="text-[#9ea5b0]">{t("Kích thước đo được", "Measured dimensions")}</dt><dd>{shown(measured.width)} x {shown(measured.height)}</dd>
      <dt className="text-[#9ea5b0]">{t("FPS yêu cầu / xác định / đo được", "FPS requested / resolved / measured")}</dt><dd>{shown(snapshot.requested_fps ?? frozenGeneration?.fps ?? snapshot.fps)} / {shown(snapshot.resolved_fps ?? frozenGeneration?.fps ?? snapshot.fps)} / {shown(measured.fps)}</dd>
      <dt className="text-[#9ea5b0]">{t("Thời lượng yêu cầu / xác định / đo được", "Duration requested / resolved / measured")}</dt><dd>{shown(snapshot.requested_duration_seconds ?? frozenDirector?.requested_duration_seconds ?? snapshot.duration_seconds ?? frozenGeneration?.duration_seconds)} / {shown(snapshot.resolved_duration_seconds ?? frozenGeneration?.duration_seconds)} / {shown(measured.video_duration_seconds ?? measured.duration_seconds)} s</dd>
      <dt className="text-[#9ea5b0]">{t("Số khung yêu cầu / xác định / đo được", "Frames requested / resolved / measured")}</dt><dd>{shown(snapshot.requested_frames ?? frozenGeneration?.frame_count ?? snapshot.frames)} / {shown(snapshot.resolved_frames ?? frozenGeneration?.frame_count)} / {shown(measured.frames ?? measured.frame_count)}</dd>
      <dt className="text-[#9ea5b0]">{t("Workflow", "Workflow")}</dt><dd>{shown(snapshot.workflow_version)}</dd>
      <dt className="text-[#9ea5b0]">{t("Phiên bản nguồn Director cố định", "Pinned Director source")}</dt><dd>{shown(frozenDirector?.provider ?? director?.provider?.id)} · {shown(frozenDirector?.source_commit ?? director?.provider?.commit)}</dd>
      <dt className="text-[#9ea5b0]">{t("Nhà cung cấp", "Provider")}</dt><dd>{shown(snapshot.provider)} · {shown(frozenDirector?.provider_version ?? snapshot.provider_version)}</dd>
      <dt className="text-[#9ea5b0]">{t("Thời gian chạy", "Execution time")}</dt><dd>{shown(measured.execution_seconds)}</dd>
      <dt className="text-[#9ea5b0]">{t("Số byte đầu ra", "Output bytes")}</dt><dd>{shown(measured.size_bytes)}</dd>
    </dl>
    <details className="text-xs"><summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">{t("Prompt đã thực thi", "Executed prompt")}</summary><pre className="mt-2 whitespace-pre-wrap break-words text-[#c3c6d7]">{snapshot.prompt ?? t("Chưa có dữ liệu", "No data")}</pre></details>
    <div className="flex flex-wrap items-center gap-2">
      {generation.status === "COMPLETED" && <>
        <Button size="sm" variant="secondary" disabled={busy || !parentAvailable || requiresAggregate} onClick={() => { if (!busy && parentAvailable && !requiresAggregate) onRegenerate(); }}>{t("Tạo lại từ cấu hình đã chụp", "Regenerate from snapshot")}</Button>
        <Button size="sm" variant="secondary" disabled={busy || !parentAvailable || requiresAggregate} onClick={() => { if (!busy && parentAvailable && !requiresAggregate) onVariation(); }}>{t("Tạo biến thể từ cấu hình đã chụp", "Create variation from snapshot")}</Button>
        {requiresAggregate && scene && <span className="text-xs text-amber-300">{aggregateGuidance(scene, scenes)}</span>}
        {!parentAvailable && <span className="text-xs text-amber-300">{t("Workflow của bản cha chưa khả dụng cho lần chạy mới.", "The parent workflow is unavailable for a new run.")}</span>}
      </>}
      <Button size="sm" variant="secondary" disabled={busy} onClick={onReuse}>{t("Dùng lại cấu hình cho cảnh", "Reuse settings for scene")}</Button>
      {download && <a className="secondary-action text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400" href={download.url} target="_blank" rel="noopener noreferrer" download>{t("Tải xuống", "Download")}</a>}
      {generation.status === "COMPLETED" && <span className="text-xs text-[#9ea5b0]">{t("Nâng cao chất lượng: chưa có nhà cung cấp được cấu hình.", "Enhancement: no provider is configured.")}</span>}
    </div>
    {error && <p role="alert" className="text-xs text-red-400">{getErrorMessage(error)}</p>}
  </div>;
}
