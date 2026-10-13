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
import { OutputMetadataSummary } from "@/components/generation/output-metadata-summary";

export function GenerationHistoryDetails({ generation, scene, scenes, onRegenerate, onVariation, onReuse, onReuseSeed, busy }: {
  scene: SceneDTO | undefined; scenes: SceneDTO[];
  generation: GenerationDTO; onRegenerate: () => void; onVariation: () => void;
  onReuse: () => void; onReuseSeed: (seed: number) => void; busy: boolean;
}) {
  const { t } = useI18n();
  const snapshot = generation.input_snapshot;
  const freshness = snapshot.generation_freshness;
  const executionGroup = freshness && typeof freshness === "object" && !Array.isArray(freshness)
    ? (freshness as Record<string, unknown>).execution_group
    : null;
  const isAggregateMember = Boolean(executionGroup && typeof executionGroup === "object");
  const recordedSeed = [
    generation.output_metadata?.resolved_global_seed,
    ...(isAggregateMember ? [] : [
      snapshot.seed,
      snapshot.director_execution?.seed,
      snapshot.director_execution_spec?.generation?.seed,
    ]),
  ].find((value): value is number =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0,
  );
  const requiresAggregate = !!scene && historyGenerationRequiresAggregate(scene, scenes, generation);
  const caps = useQuery({ queryKey: ["generation-capabilities"], queryFn: generationCapabilities });
  const parentAvailable = caps.data?.available && qualifiedCombinations(caps.data).some((item) => item.workflow_id === generation.workflow_id
    && (item.mode === generation.mode || (item.mode === "fl2v" && ["i2v_last", "i2v_first_last"].includes(generation.mode)))
    && item.execution_scope === "single_scene" && item.quality_profile === (snapshot.requested_quality_profile ?? "STANDARD")
    && (!snapshot.requested_aspect_ratio || item.aspect_ratio === snapshot.requested_aspect_ratio));
  const { data: download, error } = useQuery({ queryKey: queryKeys.assets.download(generation.output_asset_id ?? ""),
    queryFn: () => downloadAsset(generation.output_asset_id!), enabled: generation.status === "COMPLETED" && !!generation.output_asset_id });

  return <div className="space-y-3">
    <OutputMetadataSummary generation={generation} />
    <details className="text-xs"><summary className="cursor-pointer rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">{t("Prompt đã thực thi", "Executed prompt")}</summary><pre className="mt-2 whitespace-pre-wrap break-words text-[#c3c6d7]">{snapshot.prompt ?? t("Chưa có dữ liệu", "No data")}</pre></details>
    <div className="flex flex-wrap items-center gap-2">
      {generation.status === "COMPLETED" && <>
        <Button size="sm" variant="secondary" disabled={busy || !parentAvailable || requiresAggregate} onClick={() => { if (!busy && parentAvailable && !requiresAggregate) onRegenerate(); }}>{t("Tạo lại từ cấu hình đã chụp", "Regenerate from snapshot")}</Button>
        <Button size="sm" variant="secondary" disabled={busy || !parentAvailable || requiresAggregate} onClick={() => { if (!busy && parentAvailable && !requiresAggregate) onVariation(); }}>{t("Tạo biến thể từ cấu hình đã chụp", "Create variation from snapshot")}</Button>
        {requiresAggregate && scene && <span className="text-xs text-amber-300">{aggregateGuidance(scene, scenes)}</span>}
        {!parentAvailable && <span className="text-xs text-amber-300">{t("Workflow của bản cha chưa khả dụng cho lần chạy mới.", "The parent workflow is unavailable for a new run.")}</span>}
      </>}
      <Button size="sm" variant="secondary" disabled={busy} onClick={onReuse}>{t("Dùng lại cấu hình cho cảnh", "Reuse settings for scene")}</Button>
      {recordedSeed !== undefined && <Button
        size="sm" variant="secondary" disabled={busy} onClick={() => onReuseSeed(recordedSeed)}>
        {t(`Dùng lại seed ${recordedSeed}`, `Reuse seed ${recordedSeed}`)}
      </Button>}
      {download && <a className="secondary-action text-xs focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400" href={download.url} target="_blank" rel="noopener noreferrer" download>{t("Tải xuống", "Download")}</a>}
      {generation.status === "COMPLETED" && <span className="text-xs text-[#9ea5b0]">{t("Nâng cao chất lượng: chưa có nhà cung cấp được cấu hình.", "Enhancement: no provider is configured.")}</span>}
    </div>
    {error && <p role="alert" className="text-xs text-red-400">{getErrorMessage(error)}</p>}
  </div>;
}
