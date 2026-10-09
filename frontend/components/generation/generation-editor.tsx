"use client";

import { useState } from "react";
import { useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { generationCapabilities, previewPrompt } from "@/lib/api/generations";
import { getAsset, listAssets } from "@/lib/api/assets";
import { patchScene } from "@/lib/api/scenes";
import { getErrorMessage } from "@/lib/api/errors";
import type { GenerationAspectRatio, GenerationRequest, PromptPreviewDTO, SceneDTO, SceneGenerationConfig, VideoDTO } from "@/lib/api/types";
import { acceptPrompt, acceptedGenerationRequest, createPromptContext, isAcceptedPromptCurrent } from "@/lib/generation/accepted-prompt";
import type { AcceptedPrompt, PromptContext } from "@/lib/generation/accepted-prompt";
import { aggregateCapability, deriveMode, generationInputProblems, matchingQualifiedCapabilities, singleSceneCapability, qualifiedCombinations, qualifiedDirectorFeature } from "@/lib/generation/capabilities";
import { useI18n } from "@/lib/i18n";
import { directGenerationRequiresAggregate } from "@/lib/generation/eligible-scenes";
import { aggregateGuidance } from "@/lib/generation/workspace-state";
import { queryKeys } from "@/lib/query/query-keys";
import { flattenPageItems, nextPageParam } from "@/lib/api/pagination";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Alert } from "@/components/ui/alert";
import { OrderedReferences } from "./ordered-references";

const modes = [
  ["AUTO", "Tự động", "Automatic"], ["t2v", "Văn bản thành video", "Text to video"], ["i2v", "Khung đầu", "Starting frame"],
  ["i2v_last", "Khung cuối", "Ending frame"], ["i2v_first_last", "Khung đầu và cuối", "Start and end frames"], ["r2v", "Tài nguyên tham chiếu", "References"],
  ["v2v", "Video thành video", "Video to video"], ["rv2v", "Video và tham chiếu thành video", "Video and references to video"],
] as const;
const ratios: GenerationAspectRatio[] = ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9", "Custom"];
const inputClass = "w-full rounded-md border border-[#2c3038] bg-[#0b101a] p-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400";

export function GenerationEditor({ scene, scenes, video, onClose, onSubmit, isLoading }: {
  scene: SceneDTO; scenes: SceneDTO[]; video: VideoDTO; onClose: () => void;
  onSubmit: (payload: GenerationRequest) => Promise<unknown>; isLoading: boolean;
}) {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [config, setConfig] = useState<SceneGenerationConfig>(() => ({ mode: "AUTO", quality_profile: "STANDARD",
    seed_policy: "RANDOM", ...scene.generation_config, aspect_ratio: scene.generation_config?.aspect_ratio ?? video.aspect_ratio as GenerationAspectRatio }));
  const [preview, setPreview] = useState<{ context: PromptContext; data: PromptPreviewDTO; scope: "single_scene" | "aggregate" } | null>(null);
  const [text, setText] = useState("");
  const [accepted, setAccepted] = useState<AcceptedPrompt | null>(null);
  const [acceptedScope, setAcceptedScope] = useState<"single_scene" | "aggregate">("single_scene");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const capsQuery = useQuery({ queryKey: ["generation-capabilities"], queryFn: generationCapabilities });
  const assetScope = { project_id: video.project_id, product_id: video.product_id ?? undefined, status: "READY" };
  const assetsQuery = useInfiniteQuery({
    queryKey: queryKeys.assets.list({ ...assetScope, size: 50 }),
    queryFn: ({ pageParam }) => listAssets({ ...assetScope, size: 50, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: nextPageParam,
  });
  const pagedAssets = flattenPageItems(assetsQuery.data?.pages);
  const persistedAssetIds = Array.from(new Set([
    config.first_frame_asset_id, config.last_frame_asset_id, config.source_video_asset_id,
    ...(config.reference_image_asset_ids ?? []), ...(config.reference_video_asset_ids ?? []), ...(config.reference_audio_asset_ids ?? []),
  ].filter((id): id is string => Boolean(id))));
  const missingAssetIds = persistedAssetIds.filter((id) => !pagedAssets.some((asset) => asset.id === id));
  const persistedAssetQueries = useQueries({ queries: missingAssetIds.map((id) => ({
    queryKey: queryKeys.assets.detail(id), queryFn: () => getAsset(id), staleTime: 30_000,
  })) });
  const assets = [...pagedAssets, ...persistedAssetQueries.flatMap((query) => query.data ? [query.data] : [])]
    .filter((asset, index, all) => all.findIndex((candidate) => candidate.id === asset.id) === index);
  const baseDraft: GenerationRequest = { ...config, workflow_id: undefined, aspect_ratio: config.aspect_ratio ?? undefined,
    seed: config.seed_policy === "FIXED" ? config.seed : null };
  const requiresAggregate = directGenerationRequiresAggregate(scene, scenes, baseDraft);
  const scope = requiresAggregate ? "aggregate" : "single_scene";
  // Scope-bound choices become automatic immediately if the scene context changes.
  const [workflowSelection, setWorkflowSelection] = useState(() => ({ scope, workflowId: "", resolutionKey: "" }));
  if (workflowSelection.scope !== scope) {
    setWorkflowSelection({ scope, workflowId: "", resolutionKey: "" });
    setAccepted(null);
  }
  const workflowId = workflowSelection.scope === scope ? workflowSelection.workflowId : "";
  const resolutionKey = workflowSelection.scope === scope ? workflowSelection.resolutionKey : "";
  function selectWorkflow(value: string) { setWorkflowSelection({ scope, workflowId: value, resolutionKey }); }
  function selectResolution(value: string) { setWorkflowSelection({ scope, workflowId: "", resolutionKey: value }); }
  const available = matchingQualifiedCapabilities(capsQuery.data, baseDraft, scope, true);
  const resolutionCapabilities = matchingQualifiedCapabilities(capsQuery.data, baseDraft, scope, false);
  const sizeKey = (width: number, height: number) => `${width}x${height}`;
  const selectedSizeCapabilities = resolutionKey
    ? resolutionCapabilities.filter((item) => sizeKey(item.resolved_width, item.resolved_height) === resolutionKey)
    : resolutionCapabilities;
  const selectedWorkflowCapabilities = workflowId
    ? selectedSizeCapabilities.filter((item) => item.workflow_id === workflowId)
    : selectedSizeCapabilities;
  const invalidResolutionSelection = (!!resolutionKey && !selectedSizeCapabilities.length)
    || (!!workflowId && !selectedWorkflowCapabilities.length);
  const requestedWorkflowId = workflowId || (resolutionKey ? selectedWorkflowCapabilities[0]?.workflow_id : undefined);
  const draft: GenerationRequest = { ...baseDraft, workflow_id: requestedWorkflowId };
  const cap = invalidResolutionSelection ? null : singleSceneCapability(capsQuery.data, draft);
  const aggregateCap = invalidResolutionSelection ? null : aggregateCapability(capsQuery.data, draft);
  const settingsCap = requiresAggregate ? aggregateCap : cap;
  const workflows = available.filter((item, index, all) =>
    (!resolutionKey || sizeKey(item.resolved_width, item.resolved_height) === resolutionKey)
    && all.findIndex((candidate) => candidate.workflow_id === item.workflow_id) === index);
  const settings: GenerationRequest = { ...draft, ...(settingsCap ? { workflow_id: settingsCap.workflow_id } : {}) };
  const context = createPromptContext(scene.id, scene.revision, video.id, video.revision, settings);
  const customCanvasCapabilities = config.aspect_ratio === "Custom" ? resolutionCapabilities : [];
  const problems = generationInputProblems(settings, cap, assets, customCanvasCapabilities);
  const settingsProblems = generationInputProblems(draft, settingsCap, assets, customCanvasCapabilities);
  const specialEnabled = config.motion_context?.enabled || config.refine?.enabled || config.face_refine?.enabled || (config.audio_policy?.mode && config.audio_policy.mode !== "generate");
  const saveBlocked = !!specialEnabled && !!settingsProblems.length;
  const presets = settingsCap?.director_settings?.filter((item) =>
    (!item.refine?.enabled || qualifiedDirectorFeature(settingsCap, "refine"))
    && (!item.face_refine?.enabled || qualifiedDirectorFeature(settingsCap, "face_refine"))
    && (!item.motion_context?.enabled || qualifiedDirectorFeature(settingsCap, "motion_context"))) ?? [];
  const qualified = capsQuery.data?.available ? qualifiedCombinations(capsQuery.data).filter((item) => item.execution_scope === scope) : [];
  const referenceCapabilities = workflowId ? available.filter((item) => item.workflow_id === workflowId) : available;
  const refCap = referenceCapabilities.find((item) => item.mode === "r2v") ?? referenceCapabilities.find((item) => item.mode === "rv2v");
  const profileLabel = (profile: string) => ({ DRAFT: t("Bản nháp", "Draft"), STANDARD: t("Tiêu chuẩn", "Standard"), HIGH: t("Cao", "High"), BASE: t("Cơ bản", "Base"), HD: t("HD", "HD"), FULL_HD_REFINED: t("Full HD đã tinh chỉnh", "Refined Full HD"), CUSTOM: t("Tùy chỉnh", "Custom") } as Record<string, string>)[profile] ?? profile;
  const modeLabel = (mode: string) => { const item = modes.find(([value]) => value === mode); return item ? t(item[1], item[2]) : mode; };
  const audioLabel = (mode: string) => mode === "source" ? t("Âm thanh nguồn", "Source audio") : mode === "mute" ? t("Tắt tiếng", "Muted") : t("Tạo âm thanh", "Generate audio");
  const activeMode = draft.mode && draft.mode !== "AUTO" ? draft.mode : deriveMode(draft);
  const profiles = Array.from(new Set(qualified.filter((item) => item.aspect_ratio === config.aspect_ratio
    && (item.mode === activeMode || (item.mode === "fl2v" && ["i2v_last", "i2v_first_last"].includes(activeMode))))
    .map((item) => item.quality_profile)));
  const acceptedCurrent = acceptedScope === scope && isAcceptedPromptCurrent(accepted, context) && accepted?.text === text;
  const previewCurrent = preview?.scope === scope && preview && isAcceptedPromptCurrent({ context: preview.context, text: "" }, context);
  const images = assets.filter((item) => item.content_type.startsWith("image/"));
  function change(update: Partial<SceneGenerationConfig>) {
    setConfig((old) => ({ ...old, ...update }));
    if (["mode", "quality_profile", "aspect_ratio", "width", "height", "first_frame_asset_id", "last_frame_asset_id",
      "source_video_asset_id", "reference_image_asset_ids", "reference_video_asset_ids", "reference_audio_asset_ids", "motion_context"]
      .some((key) => key in update)) setWorkflowSelection({ scope, workflowId: "", resolutionKey: "" });
    setAccepted(null);
  }
  function modeChange(mode: SceneGenerationConfig["mode"]) {
    change({ mode, first_frame_asset_id: null, last_frame_asset_id: null,
      source_video_asset_id: null, reference_image_asset_ids: [], reference_video_asset_ids: [], reference_audio_asset_ids: [] });
  }
  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  async function loadPreview() {
    const captured = context;
    const data = await previewPrompt(scene.id, settings);
    setPreview({ context: captured, data, scope }); setText(data.execution_prompt); setAccepted(null);
  }
  function acceptPreview() {
    if (!preview) return;
    if (preview.data.scene_revision !== context.sceneRevision || preview.data.video_revision !== context.videoRevision) {
      throw new Error(t("Prompt đã cũ; hãy xem trước lại trước khi chấp nhận.", "The prompt is stale; preview again before accepting."));
    }
    if (!text.trim() || [...text].length > 30000) {
      throw new Error(t("Prompt phải có nội dung và tối đa 30000 ký tự.", "The prompt must contain text and at most 30000 characters."));
    }
    setAccepted(acceptPrompt(preview.context, preview.data, text));
    setAcceptedScope(scope);
  }
  async function saveConfig() {
    if (saveBlocked) throw new Error(settingsProblems.join(" "));
    await patchScene(scene.id, { generation_config: { ...config, seed: config.seed_policy === "FIXED" ? config.seed : null } }, scene.revision);
    await queryClient.invalidateQueries({ queryKey: queryKeys.videos.detail(video.id) });
    onClose();
  }
  const frameFields = (config.mode === "AUTO" ? ["first_frame_asset_id", "last_frame_asset_id"]
    : config.mode === "i2v" ? ["first_frame_asset_id"] : config.mode === "i2v_last" ? ["last_frame_asset_id"]
      : config.mode === "i2v_first_last" ? ["first_frame_asset_id", "last_frame_asset_id"] : []) as ("first_frame_asset_id" | "last_frame_asset_id")[];
  return <Dialog isOpen onClose={onClose} title={t(`Tạo video · Cảnh ${scene.scene_order + 1}`, `Generate video · Scene ${scene.scene_order + 1}`)}
    description={t("Lưu cấu hình riêng cho cảnh hoặc xem trước, chấp nhận prompt rồi tạo video.", "Save scene settings or preview and accept the prompt to generate video.")} maxWidth="lg">
    <div className="space-y-4">
      {error && <Alert variant="destructive">{error}</Alert>}
      {requiresAggregate && <Alert>{aggregateGuidance(scene, scenes)}</Alert>}
      {capsQuery.isLoading ? <p role="status">{t("Đang kiểm tra workflow…", "Checking workflows…")}</p> : capsQuery.error ? <Alert variant="destructive">{getErrorMessage(capsQuery.error)}<Button onClick={() => capsQuery.refetch()} variant="secondary">{t("Thử lại", "Retry")}</Button></Alert>
        : !capsQuery.data?.available && <Alert>{t("Chưa có workflow được xác minh. Bạn vẫn có thể lưu cấu hình cảnh; tạo video sẽ mở khi workflow được duyệt.", "No verified workflow is available. You can save scene settings; generation will become available when a workflow is approved.")}</Alert>}
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="space-y-1 text-xs">{t("Chế độ", "Mode")}<select className={inputClass} value={config.mode} onChange={(event) => modeChange(event.target.value as SceneGenerationConfig["mode"])}>
          {modes.map(([value, vi, en]) => <option key={value} value={value} disabled={value !== "AUTO" && !available.some((item) => item.mode === value || (item.mode === "fl2v" && ["i2v_last", "i2v_first_last"].includes(value)))}>{t(vi, en)}</option>)}
        </select></label>
        <label className="space-y-1 text-xs">{t("Cấu hình chất lượng", "Quality profile")}<select className={inputClass} value={config.quality_profile} onChange={(event) => change({ quality_profile: event.target.value as SceneGenerationConfig["quality_profile"] })}>
          {Array.from(new Set([...profiles, config.quality_profile ?? "STANDARD"])).map((value) => <option key={value} value={value} disabled={!profiles.includes(value)}>{profileLabel(value)}</option>)}
        </select></label>
        <label className="space-y-1 text-xs">{t("Tỷ lệ", "Aspect ratio")}<select className={inputClass} value={config.aspect_ratio ?? video.aspect_ratio} onChange={(event) => change({ aspect_ratio: event.target.value as GenerationAspectRatio })}>
          {ratios.map((value) => <option key={value} value={value}>{value === "Custom" ? t("Tùy chỉnh", "Custom") : value}</option>)}
        </select></label>
      </div>
      <label className="block space-y-1 text-xs">{t("Độ phân giải clip AI", "AI clip resolution")}
        <select className={inputClass} value={resolutionKey} onChange={(event) => {
          const next = event.target.value;
          selectResolution(next); setAccepted(null);
          if (config.aspect_ratio === "Custom" && next) {
            const selected = resolutionCapabilities.find((item) => sizeKey(item.resolved_width, item.resolved_height) === next);
            if (selected) setConfig((old) => ({ ...old, width: selected.resolved_width, height: selected.resolved_height }));
          }
        }}>
          <option value="">{t("Tự động chọn độ phân giải phù hợp", "Automatically select a compatible resolution")}</option>
          {resolutionCapabilities.filter((item, index, all) => all.findIndex((candidate) => sizeKey(candidate.resolved_width, candidate.resolved_height) === sizeKey(item.resolved_width, item.resolved_height)) === index)
            .map((item) => <option key={sizeKey(item.resolved_width, item.resolved_height)} value={sizeKey(item.resolved_width, item.resolved_height)}>{item.resolved_width}×{item.resolved_height} px</option>)}
        </select>
      </label>
      <details className="rounded-md border border-[#2c3038] p-3">
        <summary className="cursor-pointer text-sm font-medium rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">{t("Lựa chọn workflow nâng cao", "Advanced workflow selection")}</summary>
        <label className="mt-3 block space-y-1 text-xs">{t("Workflow đã xác minh (không bắt buộc)", "Verified workflow (optional)")}
          <select className={inputClass} value={workflowId} onChange={(event) => { selectWorkflow(event.target.value); setAccepted(null); }}>
            <option value="">{t("Tự động chọn workflow phù hợp", "Automatically select a compatible workflow")}</option>
            {workflowId && !workflows.some((item) => item.workflow_id === workflowId) && <option value={workflowId} disabled>{t("Workflow đã chọn không còn phù hợp", "Selected workflow is no longer compatible")}</option>}
            {workflows.map((item) => <option key={item.workflow_id} value={item.workflow_id} disabled={requiresAggregate}>{item.workflow_version} · {item.resolved_width}×{item.resolved_height} · {item.workflow_id}</option>)}
          </select>
        </label>
        <p className="mt-2 text-xs text-[#9ea5b0]">{t("Lựa chọn workflow chỉ áp dụng cho lần tạo này, không được lưu vào cấu hình cảnh. Tạo toàn bộ tự động chọn workflow phù hợp.", "The workflow choice applies only to this generation and is not saved in scene settings. Generate all automatically selects compatible workflows.")}</p>
      </details>
      {config.aspect_ratio === "Custom" && <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs">{t("Chiều rộng tùy chỉnh", "Custom width")}<input className={inputClass} type="number" min="32" max="8192" step="32" value={config.width ?? ""} onChange={(event) => change({ width: event.target.value === "" ? null : Number(event.target.value) })} /></label>
        <label className="space-y-1 text-xs">{t("Chiều cao tùy chỉnh", "Custom height")}<input className={inputClass} type="number" min="32" max="8192" step="32" value={config.height ?? ""} onChange={(event) => change({ height: event.target.value === "" ? null : Number(event.target.value) })} /></label>
      </div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs">{t("Seed", "Seed")}<select className={inputClass} value={config.seed_policy} onChange={(event) => change({ seed_policy: event.target.value as SceneGenerationConfig["seed_policy"] })}><option value="RANDOM">{t("Ngẫu nhiên", "Random")}</option><option value="FIXED">{t("Cố định", "Fixed")}</option></select></label>
        {config.seed_policy === "FIXED" && <label className="space-y-1 text-xs">{t("Giá trị seed", "Seed value")}<input className={inputClass} type="number" min="0" max={Number.MAX_SAFE_INTEGER} step="1" value={config.seed ?? ""} onChange={(event) => {
          const value = event.target.value === "" ? null : Number(event.target.value);
          change({ seed: value === null || (Number.isSafeInteger(value) && value >= 0) ? value : null });
        }} /></label>}
      </div>
      {assetsQuery.error && <Alert variant="destructive">{getErrorMessage(assetsQuery.error)}</Alert>}
      {assetsQuery.hasNextPage && <Button type="button" variant="secondary" disabled={assetsQuery.isFetchingNextPage}
        onClick={() => { void assetsQuery.fetchNextPage(); }}>
        <span>{assetsQuery.isFetchingNextPage ? t("Đang tải tài nguyên…", "Loading assets…") : t("Tải thêm tài nguyên", "Load more assets")}</span>
      </Button>}
      {frameFields.map((field) => <label key={field} className="block space-y-1 text-xs">{field === "first_frame_asset_id" ? t("Khung đầu", "First frame") : t("Khung cuối", "Last frame")}
        <select className={inputClass} value={config[field] ?? ""} onChange={(event) => change({ [field]: event.target.value || null })}><option value="">{t("Không chọn", "None")}</option>{images.map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}</select></label>)}
      {(config.mode === "AUTO" || config.mode === "v2v" || config.mode === "rv2v") && <label className="block space-y-1 text-xs">{t("Video nguồn", "Source video")}
        <select className={inputClass} value={config.source_video_asset_id ?? ""} onChange={(event) => change({ source_video_asset_id: event.target.value || null })}>
          <option value="">{t("Không chọn", "None")}</option>{assets.filter((item) => item.content_type.startsWith("video/")).map((asset) => <option key={asset.id} value={asset.id}>{asset.filename}</option>)}
        </select></label>}
      {(config.mode === "AUTO" || config.mode === "r2v" || config.mode === "rv2v") && <div className="grid gap-3">
        <OrderedReferences label={t("Ảnh tham chiếu", "Reference images")} tag="Image" assets={images} selected={config.reference_image_asset_ids ?? []} limit={refCap?.max_reference_images ?? 0} onChange={(ids) => change({ reference_image_asset_ids: ids })} />
        <OrderedReferences label={t("Video tham chiếu", "Reference videos")} tag="Video" assets={assets.filter((item) => item.content_type.startsWith("video/"))} selected={config.reference_video_asset_ids ?? []} limit={refCap?.max_reference_videos ?? 0} onChange={(ids) => change({ reference_video_asset_ids: ids })} />
        <OrderedReferences label={t("Âm thanh tham chiếu", "Reference audio")} tag="Audio" assets={assets.filter((item) => item.content_type.startsWith("audio/"))} selected={config.reference_audio_asset_ids ?? []} limit={refCap?.max_reference_audio ?? 0} onChange={(ids) => change({ reference_audio_asset_ids: ids })} />
      </div>}
      <details className="rounded-md border border-[#2c3038] p-3">
        <summary className="cursor-pointer text-sm font-medium rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400">{t("Thiết lập Director nâng cao", "Advanced Director settings")}</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {!!presets.length && <label className="space-y-1 text-xs sm:col-span-2">{t("Cấu hình Director đã xác minh", "Verified Director configuration")}
            <select className={inputClass} value="" onChange={(event) => {
              const selected = presets[Number(event.target.value)];
              if (selected && event.target.value !== "") change(selected);
            }}>
              <option value="">{t("Chọn cấu hình", "Choose configuration")}</option>
              {presets.map((item, index) => <option key={index} value={index}>
                {item.refine?.enabled ? t(`Tinh chỉnh ${item.refine.mode ?? "refine"} · ${item.refine.megapixels ? `${item.refine.megapixels} MP` : "MP mặc định"}, theo tỷ lệ Director`, `Refinement ${item.refine.mode ?? "refine"} · ${item.refine.megapixels ? `${item.refine.megapixels} MP` : "Default MP"}, following the Director aspect ratio`) : t("Cơ bản", "Base")}
                {item.face_refine?.enabled ? t(" · Tinh chỉnh khuôn mặt", " · Face refinement") : ""}{item.motion_context?.enabled ? t(" · Ngữ cảnh chuyển động", " · Motion context") : ""}
                {` · ${audioLabel(item.audio_policy?.mode ?? "generate")}`}
              </option>)}
            </select>
          </label>}
          {config.refine?.enabled && <>
            <dl className="text-xs space-y-1 sm:col-span-2">
              <div><dt className="inline">{t("Chế độ tinh chỉnh: ", "Refinement mode: ")}</dt><dd className="inline">{config.refine.mode ?? "refine"}</dd></div>
              <div><dt className="inline">{t("Phương pháp tăng độ phân giải: ", "Upscale method: ")}</dt><dd className="inline">{config.refine.upscale_method ?? "h3_latent"}</dd></div>
              <div><dt className="inline">{t("Số megapixel mục tiêu: ", "Target megapixels: ")}</dt><dd className="inline">{config.refine.megapixels ? `${config.refine.megapixels} MP` : t("Mặc định từ cấu hình", "Configuration default")} · {t("theo tỷ lệ Director", "following the Director aspect ratio")}</dd></div>
            </dl>
            <p className="text-xs text-[#9ea5b0] sm:col-span-2">{t("Chọn cấu hình đã xác minh để đổi tinh chỉnh. Kích thước theo tỷ lệ Director và số megapixel mục tiêu.", "Choose a verified configuration to change refinement. Dimensions follow the Director aspect ratio and target megapixels.")}</p>
          </>}
          {config.face_refine?.enabled && <label className="space-y-1 text-xs">{t("Độ tin cậy nhận diện khuôn mặt", "Face detection confidence")}
            <input className={inputClass} disabled={!qualifiedDirectorFeature(settingsCap, "face_refine")} type="number" min={0.05} max={0.95} step={0.05} value={config.face_refine.confidence ?? 0.35} onChange={(event) => change({ face_refine: { ...config.face_refine, enabled: true, confidence: Number(event.target.value) } })} />
          </label>}
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={!qualifiedDirectorFeature(aggregateCap, "motion_context") && !config.motion_context?.enabled} checked={config.motion_context?.enabled ?? false} onChange={(event) => change({ motion_context: { ...config.motion_context, enabled: event.target.checked } })} /> {t("Ngữ cảnh chuyển động", "Motion context")}</label>
          <label className="space-y-1 text-xs">{t("Số khung ngữ cảnh", "Context frames")}<select className={inputClass} disabled={!config.motion_context?.enabled || !qualifiedDirectorFeature(aggregateCap, "motion_context")} value={config.motion_context?.context_frames ?? 22} onChange={(event) => change({ motion_context: { ...config.motion_context, enabled: true, context_frames: Number(event.target.value) as 5 | 22 | 39 | 56 } })}>{[5, 22, 39, 56].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={!qualifiedDirectorFeature(settingsCap, "refine") && !config.refine?.enabled} checked={config.refine?.enabled ?? false} onChange={(event) => change({ refine: { ...config.refine, enabled: event.target.checked } })} /> {t("Tinh chỉnh", "Refinement")}</label>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" disabled={!qualifiedDirectorFeature(settingsCap, "face_refine") && !config.face_refine?.enabled} checked={config.face_refine?.enabled ?? false} onChange={(event) => change({ face_refine: { ...config.face_refine, enabled: event.target.checked } })} /> {t("Tinh chỉnh khuôn mặt", "Face refinement")}</label>
          <label className="space-y-1 text-xs">{t("Chính sách âm thanh", "Audio policy")}<select className={inputClass} value={config.audio_policy?.mode ?? "generate"} onChange={(event) => change({ audio_policy: { ...config.audio_policy, mode: event.target.value as "generate" | "source" | "mute" } })}><option value="generate">{t("Tạo âm thanh gốc", "Generate native audio")}</option><option value="source" disabled={!settingsCap?.audio_modes?.includes("source")}>{t("Dùng âm thanh nguồn", "Use source audio")}</option><option value="mute" disabled={!settingsCap?.audio_modes?.includes("mute")}>{t("Tắt tiếng", "Mute")}</option></select></label>
        </div>
      </details>
      {capsQuery.data?.source_capabilities && <p className="text-xs text-[#9ea5b0]">{t(`Nguồn hỗ trợ ${capsQuery.data.source_capabilities.tasks?.length ?? 0} tác vụ; hiện có ${qualified.length} tổ hợp được xác minh.`, `The source supports ${capsQuery.data.source_capabilities.tasks?.length ?? 0} tasks; ${qualified.length} combinations are currently verified.`)}</p>}
      <p className="text-xs text-[#9ea5b0]">{t("Chế độ được xác định", "Resolved mode")}: {modeLabel(deriveMode(settings))}{settingsCap && t(` · ${settingsCap.resolved_width}×${settingsCap.resolved_height} dự kiến · ${settingsCap.fps} FPS · ${settingsCap.workflow_version}`, ` · Expected ${settingsCap.resolved_width}×${settingsCap.resolved_height} · ${settingsCap.fps} FPS · ${settingsCap.workflow_version}`)} · {t(`${scene.duration_seconds} giây`, `${scene.duration_seconds} seconds`)}</p>
      {(requiresAggregate ? settingsProblems : problems).length > 0 && <ul className="list-disc pl-5 text-xs text-amber-300" aria-live="polite">{(requiresAggregate ? settingsProblems : problems).map((item) => <li key={item}>{item}</li>)}</ul>}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" isLoading={busy} disabled={isLoading || saveBlocked} onClick={() => run(saveConfig)}>{t("Lưu cấu hình cảnh", "Save scene settings")}</Button>
        <Button variant="secondary" disabled={requiresAggregate || !!problems.length || isLoading || busy} onClick={() => run(loadPreview)}>{t("Xem trước prompt", "Preview prompt")}</Button>
      </div>
      {preview && <div className="space-y-3 border-t border-[#2c3038] pt-4">
        {!previewCurrent && <Alert>{t("Cảnh hoặc cấu hình đã đổi. Hãy xem trước prompt lại.", "The scene or settings changed. Preview the prompt again.")}</Alert>}
        <label className="block space-y-2 text-xs">{t("Prompt thực thi · chỉnh sửa trước khi chấp nhận", "Execution prompt · edit before accepting")}<textarea className={`${inputClass} min-h-36`} value={text} onChange={(event) => { setText(event.target.value); setAccepted(null); }} maxLength={60000} /></label>
        {preview.data.warnings.map((warning, index) => <p key={index} className="text-xs text-amber-300">{warning}</p>)}
        <Button variant="secondary" disabled={!previewCurrent || !!problems.length || busy || isLoading} onClick={() => run(async () => acceptPreview())}>{acceptedCurrent ? t("Đã chấp nhận prompt", "Prompt accepted") : t("Chấp nhận nguyên văn prompt", "Accept exact prompt")}</Button>
        <Button disabled={requiresAggregate || !acceptedCurrent || !!problems.length || busy} isLoading={isLoading} onClick={() => run(async () => { if (!requiresAggregate && acceptedCurrent && !problems.length && accepted) await onSubmit(acceptedGenerationRequest(accepted, context)); })}>{t("Tạo video với prompt đã chấp nhận", "Generate video with accepted prompt")}</Button>
      </div>}
    </div>
  </Dialog>;
}
