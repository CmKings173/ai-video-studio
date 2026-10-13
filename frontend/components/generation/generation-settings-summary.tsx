import type { ResolvedGenerationConfiguration } from "@/lib/api/types";
import { translateText as t } from "@/lib/i18n";
import { formatResolvedResolution } from "@/lib/generation/resolution-labels";

function valueOrDash(value: string | number | null | undefined): string {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

export function GenerationSettingsSummary({ configuration }: {
  configuration?: ResolvedGenerationConfiguration | null;
}) {
  if (!configuration) {
    return <p role="status" className="rounded-md border border-amber-500/40 bg-amber-950/20 p-3 text-sm text-amber-200">
      {t("Máy chủ chưa trả về cấu hình thực thi đã phân giải. Chưa thể chấp nhận prompt hoặc tạo video.",
        "The server did not return a resolved execution configuration. Prompt acceptance and generation are unavailable.")}
    </p>;
  }

  const items: Array<[string, string]> = [
    [t("Chế độ yêu cầu → thực thi", "Requested → resolved mode"), `${valueOrDash(configuration.requested_mode)} → ${valueOrDash(configuration.resolved_mode)}`],
    [t("Chất lượng đã xác minh", "Qualified quality"), valueOrDash(configuration.quality_label ?? configuration.quality_profile)],
    [t("Độ phân giải gốc", "Native resolution"), formatResolvedResolution(configuration, "native") ?? "—"],
    [t("Đầu ra cuối", "Final output"), formatResolvedResolution(configuration, "output") ?? "—"],
    [t("Tỉ lệ khung hình", "Aspect ratio"), valueOrDash(configuration.aspect_ratio)],
    [t("Workflow", "Workflow"), [configuration.workflow_code, configuration.workflow_version].filter(Boolean).join(" · ") || valueOrDash(configuration.workflow_id)],
    [t("Sampler / scheduler", "Sampler / scheduler"), [configuration.sampler, configuration.scheduler].filter(Boolean).join(" / ") || "—"],
    [t("Bước lấy mẫu / CFG", "Sampling steps / CFG"), [configuration.sampling_steps, configuration.cfg].map(valueOrDash).join(" / ")],
    [t("Mô hình / runtime", "Model / runtime"), [configuration.model_identity, configuration.runtime_identity].filter(Boolean).join(" · ") || "—"],
    [t("Tốc độ / số khung / thời lượng", "FPS / frames / duration"), [
      configuration.fps == null ? "—" : `${configuration.fps} FPS`,
      configuration.frames == null ? "—" : `${configuration.frames} ${t("khung", "frames")}`,
      configuration.duration_seconds == null ? "—" : `${configuration.duration_seconds} ${t("giây", "s")}`,
    ].join(" · ")],
    [t("Seed", "Seed"), configuration.seed_policy === "FIXED"
      ? `${t("Cố định", "Fixed")}: ${valueOrDash(configuration.fixed_seed_or_null)}`
      : t("Ngẫu nhiên", "Random")],
    [t("Tinh chỉnh / nâng độ phân giải", "Refinement / upscale"), configuration.refine_enabled || configuration.upscale_enabled
      ? [configuration.refine_mode, configuration.upscale_enabled ? t("Có", "Yes") : null].filter(Boolean).join(" · ")
      : t("Không", "No")],
    [t("Trạng thái qualification", "Qualification status"), valueOrDash(configuration.qualification_status)],
  ];

  return <section aria-label={t("Cấu hình thực thi do máy chủ phân giải", "Server-resolved execution settings")}
    className="space-y-3 rounded-md border border-[#2c3038] bg-[#10151e] p-3">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold">{t("Cấu hình thực tế", "Resolved settings")}</h3>
      <span className={configuration.runnable ? "text-xs text-emerald-300" : "text-xs text-amber-200"}>
        {configuration.runnable ? t("Đủ điều kiện chạy", "Runnable") : t("Chưa thể chạy", "Not runnable")}
      </span>
    </div>
    {configuration.quality_description && <p className="text-xs text-[#c1c7d0]">{configuration.quality_description}</p>}
    {configuration.resolution_display_label && <p className="text-xs text-[#c1c7d0]">{configuration.resolution_display_label}</p>}
    {!configuration.runnable && configuration.unavailable_reason && <p role="status" className="text-xs text-amber-200">{configuration.unavailable_reason}</p>}
    <dl className="grid gap-x-5 gap-y-2 text-xs sm:grid-cols-2">
      {items.map(([label, value]) => <div key={label} className="min-w-0">
        <dt className="text-[#9ea5b0]">{label}</dt>
        <dd className="break-words text-[#eef0f4]">{value}</dd>
      </div>)}
    </dl>
    {configuration.configuration_hash && <p className="break-all text-[11px] text-[#818997]">
      {t("Mã cấu hình", "Configuration hash")}: {configuration.configuration_hash}
    </p>}
  </section>;
}
