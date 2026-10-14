import type { GenerationDTO } from "@/lib/api/types";
import { useI18n } from "@/lib/i18n";
import { formatDimensions, formatHistoricalResolution } from "@/lib/generation/resolution-labels";

type JsonRecord = Record<string, unknown>;
type Fact = readonly [label: string, value: string];

function record(value: unknown): JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : {};
}

function firstRecorded(...values: unknown[]): unknown {
  return values.find((value) => value !== undefined && value !== null);
}

function scalar(value: unknown, missing: string): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return missing;
}

function qualityLabel(value: unknown, savedLabel: unknown, missing: string, t: (vi: string, en: string) => string): string {
  if (typeof savedLabel === "string" && savedLabel.trim()) return savedLabel;
  if (typeof value !== "string" || !value.trim()) return missing;
  const labels: Record<string, string> = {
    DRAFT: t("Bản nháp", "Draft"), STANDARD: t("Tiêu chuẩn", "Standard"), HIGH: t("Cao", "High"),
    BASE: t("Cơ bản", "Base"), HD: "HD", FULL_HD_REFINED: t("Full HD đã tinh chỉnh", "Refined Full HD"),
    CUSTOM: t("Tùy chỉnh", "Custom"),
  };
  return labels[value] ?? value;
}

function dimensions(width: unknown, height: unknown, missing: string): string {
  return formatDimensions(width, height) ?? missing;
}

function enabled(value: unknown, t: (vi: string, en: string) => string): string {
  return typeof value === "boolean"
    ? value ? t("Bật", "Enabled") : t("Tắt", "Disabled")
    : t("Chưa ghi nhận", "Unknown");
}

function measuredBoolean(value: unknown, missing: string, t: (vi: string, en: string) => string): string {
  return typeof value === "boolean"
    ? value ? t("Có", "Yes") : t("Không", "No")
    : missing;
}

function Section({ title, facts }: { title: string; facts: Fact[] }) {
  return (
    <section className="rounded-md border border-[#2b3039] bg-[#111318]/60 p-3">
      <h3 className="mb-2 text-xs font-semibold text-[#e5e7eb]">{title}</h3>
      <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-[#9ea5b0]">{label}</dt>
            <dd className="break-words text-[#e5e7eb]">{value}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/** Historical facts come only from the generation's frozen snapshot and output metadata. */
export function OutputMetadataSummary({ generation }: { generation: GenerationDTO }) {
  const { t } = useI18n();
  const unknown = t("Chưa ghi nhận", "Unknown");
  const noMeasurement = t("Chưa có số đo", "No recorded measurement");
  const noExpectedFinal = t("Chưa ghi nhận kích thước đầu ra cuối", "Final output dimensions not recorded");
  const snapshot = record(generation.input_snapshot);
  const executionGroup = record(record(snapshot.generation_freshness).execution_group);
  const isAggregateMember = Object.keys(executionGroup).length > 0;
  const resolvedConfiguration = record(firstRecorded(generation.resolved_configuration, snapshot.resolved_configuration));
  const resolved: JsonRecord = {
    ...resolvedConfiguration,
    native_width: firstRecorded(resolvedConfiguration.native_width, snapshot.resolved_width, snapshot.width),
    native_height: firstRecorded(resolvedConfiguration.native_height, snapshot.resolved_height, snapshot.height),
  };
  const frozen = record(snapshot.director_execution);
  const directorSpec = record(snapshot.director_execution_spec);
  const directorGeneration = record(directorSpec.generation);
  const metadata = record(generation.output_metadata);
  const measured = record(metadata.measured);
  const workflow = record(metadata.workflow);
  const frozenRefine = record(firstRecorded(frozen.refine, snapshot.refine, directorSpec.refine));
  const faceRefine = record(firstRecorded(frozen.face_refine, snapshot.face_refine, directorSpec.face_refine));
  const audio = record(firstRecorded(frozen.audio_policy, snapshot.audio_policy, directorSpec.audio, snapshot.audio));
  const refineMode = firstRecorded(frozenRefine.mode, resolved.refine_mode);
  const recordedRefineEnabled = firstRecorded(resolved.refine_enabled, frozenRefine.enabled);
  const recordedUpscaleEnabled = firstRecorded(
    resolved.upscale_enabled,
    typeof refineMode === "string" ? ["upscale", "latent_upscale"].includes(refineMode) : undefined,
  );
  const expectedOutputDimensions = formatDimensions(
    firstRecorded(resolved.expected_output_width, snapshot.expected_output_width),
    firstRecorded(resolved.expected_output_height, snapshot.expected_output_height),
  ) ?? (recordedRefineEnabled === false && recordedUpscaleEnabled !== true
    ? formatHistoricalResolution(snapshot, "native")
    : null);
  const audioMode = firstRecorded(audio.mode, typeof audio.enabled === "boolean" ? audio.enabled ? "generate" : "mute" : undefined);
  const audioModeLabel = audioMode === "generate" ? t("Tạo âm thanh", "Generate audio")
    : audioMode === "source" ? t("Dùng âm thanh nguồn", "Use source audio")
      : audioMode === "mute" ? t("Tắt âm thanh", "Mute audio") : scalar(audioMode, unknown);
  const operationLabel = generation.operation === "ORIGINAL" ? t("Tạo mới", "Original")
    : generation.operation === "REGENERATE" ? t("Tạo lại", "Regenerate")
      : generation.operation === "VARIATION" ? t("Biến thể", "Variation") : scalar(generation.operation, unknown);
  const status = scalar(generation.status, unknown);

  const requestedFacts: Fact[] = [
    [t("Thao tác", "Operation"), operationLabel],
    [t("Chế độ yêu cầu", "Requested mode"), scalar(firstRecorded(resolved.requested_mode, snapshot.requested_mode), unknown)],
    [t("Chất lượng yêu cầu", "Requested quality"), qualityLabel(snapshot.requested_quality_profile, undefined, unknown, t)],
    [t("Kích thước yêu cầu", "Requested dimensions"), dimensions(
      snapshot.requested_width,
      snapshot.requested_height, unknown,
    )],
    [t("Thời lượng yêu cầu (giây)", "Requested duration (seconds)"), scalar(snapshot.requested_duration_seconds, unknown)],
    [t("FPS yêu cầu", "Requested FPS"), scalar(snapshot.requested_fps, unknown)],
    [t("Số khung yêu cầu", "Requested frames"), scalar(snapshot.requested_frames, unknown)],
  ];

  const resolvedFacts: Fact[] = [
    [t("Chế độ thực thi", "Resolved mode"), scalar(resolved.resolved_mode, unknown)],
    [t("Chất lượng đã phân giải", "Resolved quality"), qualityLabel(resolved.quality_profile, resolved.quality_label, unknown, t)],
    [t("Workflow (mã / phiên bản)", "Workflow (code / version)"), [
      scalar(firstRecorded(resolved.workflow_code, snapshot.workflow_code, workflow.code), unknown),
      scalar(firstRecorded(resolved.workflow_version, snapshot.workflow_version, workflow.version), unknown),
    ].join(" / ")],
    [t("Workflow ID", "Workflow ID"), scalar(firstRecorded(resolved.workflow_id, snapshot.workflow_id, workflow.id), unknown)],
    [t("Độ phân giải gốc", "Native resolution"), formatHistoricalResolution(snapshot, "native")
      ?? dimensions(resolved.native_width, resolved.native_height, unknown)],
    [t("Đầu ra cuối dự kiến", "Expected final output"), expectedOutputDimensions ?? noExpectedFinal],
    [t("Bước lấy mẫu / CFG", "Sampling steps / CFG"), [
      scalar(resolved.sampling_steps, unknown),
      scalar(resolved.cfg, unknown),
    ].join(" / ")],
    [t("FPS đã phân giải", "Resolved FPS"), scalar(firstRecorded(resolved.fps, snapshot.resolved_fps), unknown)],
    [t("Thời lượng đã phân giải (giây)", "Resolved duration (seconds)"), scalar(firstRecorded(resolved.duration_seconds, snapshot.resolved_duration_seconds), unknown)],
    [t("Số khung đã phân giải", "Resolved frames"), scalar(firstRecorded(resolved.frames, snapshot.resolved_frames), unknown)],
    [t("Tinh chỉnh", "Refinement"), enabled(resolved.refine_enabled, t)],
    [t("Tăng độ phân giải", "Upscaling"), enabled(firstRecorded(resolved.upscale_enabled,
      typeof refineMode === "string" ? ["upscale", "latent_upscale"].includes(refineMode) : undefined), t)],
  ];

  const frozenFacts: Fact[] = [
    [t("Chế độ đã lưu", "Frozen mode"), scalar(snapshot.mode, unknown)],
    [t("Chất lượng đã lưu", "Frozen quality"), qualityLabel(snapshot.requested_quality_profile, undefined, unknown, t)],
    [t("Workflow đã lưu (mã / phiên bản)", "Frozen workflow (code / version)"), [
      scalar(firstRecorded(snapshot.workflow_code, workflow.code), unknown),
      scalar(firstRecorded(snapshot.workflow_version, workflow.version), unknown),
    ].join(" / ")],
    [t("Tác vụ", "Task"), scalar(firstRecorded(frozen.task, snapshot.task, record(directorSpec.intent).director_task), unknown)],
    [t("Nhà cung cấp / phiên bản", "Provider / version"), [
      scalar(firstRecorded(frozen.provider, record(directorSpec.provider).id, snapshot.provider), unknown),
      scalar(firstRecorded(frozen.provider_version, snapshot.provider_version), unknown),
    ].join(" / ")],
    [t("Mã nguồn Director đã khóa", "Pinned Director source"), scalar(firstRecorded(frozen.source_commit, record(directorSpec.provider).commit), unknown)],
    [t("FPS đã đóng băng", "Frozen FPS"), scalar(firstRecorded(frozen.fps, snapshot.fps, directorGeneration.fps), unknown)],
    [t("Thời lượng đã đóng băng (giây)", "Frozen duration (seconds)"), scalar(firstRecorded(frozen.resolved_duration_seconds, snapshot.resolved_duration_seconds, snapshot.duration_seconds, directorGeneration.duration_seconds), unknown)],
    [t("Số khung đã đóng băng", "Frozen frames"), scalar(firstRecorded(frozen.frames, snapshot.resolved_frames, snapshot.frames, directorGeneration.frame_count), unknown)],
    [t("Bước lấy mẫu / CFG đã đóng băng", "Frozen sampling steps / CFG"), [
      scalar(firstRecorded(frozen.steps, snapshot.steps, directorGeneration.steps), unknown),
      scalar(firstRecorded(frozen.cfg, snapshot.cfg, directorGeneration.cfg), unknown),
    ].join(" / ")],
    [t("Chính sách seed", "Seed policy"), scalar(firstRecorded(snapshot.seed_policy, resolved.seed_policy), unknown)],
    [t("Seed thực tế đã lưu", "Recorded execution seed"), scalar(
      isAggregateMember
        ? metadata.resolved_global_seed
        : firstRecorded(metadata.resolved_global_seed, frozen.seed, snapshot.seed, directorGeneration.seed),
      unknown,
    )],
    [t("Seed chung của nhóm", "Aggregate global seed"), scalar(metadata.resolved_global_seed, unknown)],
    [t("Lần chạy Director", "Director run"), scalar(metadata.director_run_id, unknown)],
    [t("Tinh chỉnh đã đóng băng", "Frozen refinement"), `${enabled(frozenRefine.enabled ?? resolved.refine_enabled, t)} · ${scalar(refineMode, unknown)}`],
    [t("Tinh chỉnh khuôn mặt", "Face refinement"), enabled(faceRefine.enabled, t)],
    [t("Âm thanh đã đóng băng", "Frozen audio policy"), audioModeLabel],
  ];

  const measuredWidth = firstRecorded(metadata.measured_width, measured.width, metadata.width);
  const measuredHeight = firstRecorded(metadata.measured_height, measured.height, metadata.height);
  const measuredFacts: Fact[] = [
    [t("Kích thước đo được", "Measured dimensions"), dimensions(measuredWidth, measuredHeight, noMeasurement)],
    [t("FPS đo được", "Measured FPS"), scalar(firstRecorded(metadata.measured_fps, measured.fps, metadata.fps), noMeasurement)],
    [t("Thời lượng đo được (giây)", "Measured duration (seconds)"), scalar(firstRecorded(metadata.measured_duration_seconds, measured.duration_seconds, metadata.duration_seconds), noMeasurement)],
    [t("Số khung đo được", "Measured frames"), scalar(firstRecorded(metadata.measured_frame_count, measured.frames, metadata.frame_count), noMeasurement)],
    [t("Có hình ảnh", "Video track"), measuredBoolean(firstRecorded(metadata.measured_has_video, measured.has_video, metadata.has_video), noMeasurement, t)],
    [t("Có âm thanh", "Audio track"), measuredBoolean(firstRecorded(metadata.measured_has_audio, measured.has_audio, metadata.has_audio), noMeasurement, t)],
    [t("Thời gian chạy (giây)", "Execution time (seconds)"), scalar(metadata.execution_seconds, noMeasurement)],
    [t("Dung lượng", "File size"), scalar(metadata.size_bytes, noMeasurement)],
    [t("SHA-256", "SHA-256 checksum"), scalar(metadata.checksum, noMeasurement)],
  ];

  return (
    <div role="group" aria-label={t("Thông tin lịch sử tạo video", "Generation history details")} className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        <span className="text-[#9ea5b0]">{t("Trạng thái", "Status")}</span>
        <span className="rounded border border-[#343a45] px-2 py-0.5 text-[#e5e7eb]">{status}</span>
        {generation.phase && <span className="text-[#9ea5b0]">{t("Giai đoạn", "Phase")}: {generation.phase}</span>}
      </div>
      <Section title={t("Yêu cầu ban đầu", "Original request")} facts={requestedFacts} />
      <Section title={t("Cấu hình đã phân giải", "Resolved configuration")} facts={resolvedFacts} />
      <Section title={t("Cấu hình thực thi đã lưu", "Frozen execution configuration")} facts={frozenFacts} />
      <Section title={t("Đầu ra đo được", "Measured output")} facts={measuredFacts} />
    </div>
  );
}
