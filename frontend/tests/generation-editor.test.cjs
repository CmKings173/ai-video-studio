const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

function setup(options = {}) {
  const states = []; let cursor = 0; const sent = []; const previews = []; const saved = [];
  const scene = { id: "scene", enabled: true, spec: { continuity: "CUT" }, revision: 2, scene_order: 0, duration_seconds: 5, generation_config: options.sceneConfig ?? {} };
  const video = { id: "video", revision: 4, aspect_ratio: "9:16", project_id: "project", scenes: [scene] };
  const cap = { workflow_id: "synthetic-test-only", execution_scope: "single_scene", mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16", required_asset_slots: [], max_reference_images: 0, max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0 };
  const editor = loadTypeScript("components/generation/generation-editor.tsx", {
    react: { ...React, useState: (initial) => { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial; return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; } },
    "@tanstack/react-query": { useQueryClient: () => ({ invalidateQueries: async () => {} }), useQueries: ({ queries }) => queries.map((query) => ({ data: (options.hydratedAssets ?? []).find((asset) => asset.id === query.queryKey.at(-1)) })), useInfiniteQuery: () => ({ data: { pages: [{ items: options.pageAssets ?? [], total: (options.pageAssets ?? []).length, page: 1, page_size: 50 }] }, hasNextPage: false, isFetchingNextPage: false, fetchNextPage: async () => {} }), useQuery: () => ({ data: options.capabilities ?? { available: true, combinations: options.combinations ?? [cap, { ...cap, quality_profile: "HIGH" }] } }) },
    "@/lib/api/scenes": { patchScene: async (...args) => saved.push(args) },
    "@/lib/api/generations": { generationCapabilities: async () => {}, previewPrompt: (id, payload) => new Promise((resolve) => previews.push({ id, payload, resolve })) },
  }).GenerationEditor;
  let tree;
  const render = () => { cursor = 0; tree = editor({ scene, scenes: video.scenes, video, isLoading: false, onClose: () => {}, onSubmit: async (payload) => sent.push(payload) }); return tree; };
  function nodes(node) { if (!node || typeof node !== "object") return []; return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
  const button = (label) => { const found = nodes(tree).find((node) => node.props?.children === label); assert.ok(found, label); return found; };
  const changeSelect = (value, next) => { const found = nodes(tree).find((node) => node.type === "select" && node.props.value === value); assert.ok(found); found.props.onChange({ target: { value: next } }); render(); };
  const resolvePreview = async (pending) => { previews.at(-1).resolve({ raw_prompt: "raw", execution_prompt: "  composed\n  ", scene_revision: 2, video_revision: 4, warnings: [], enhanced: false }); await pending; render(); };
  render(); return { render, scene, video, sent, saved, previews, button, changeSelect, resolvePreview,
    edit: (text) => { nodes(tree).find((node) => node.type === "textarea").props.onChange({ target: { value: text } }); render(); } };
}

test("editor sends exact edited accepted prompt with the preview's revision pair and chosen workflow", async () => {
  const h = setup(); const pending = h.button("Xem trước prompt").props.onClick(); await h.resolvePreview(pending);
  h.edit("  edited\n\tcafé  "); await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, false);
  await h.button("Tạo video với prompt đã chấp nhận").props.onClick();
  assert.equal(h.sent[0].execution_prompt, "  edited\n\tcafé  ");
  assert.equal(h.sent[0].source_scene_revision, 2); assert.equal(h.sent[0].source_video_revision, 4);
  assert.equal(h.sent[0].workflow_id, "synthetic-test-only");
});

test("persisted asset IDs hydrate even when they are absent from the first asset page", () => {
  const h = setup({
    sceneConfig: { mode: "AUTO", first_frame_asset_id: "persisted-image" },
    pageAssets: [],
    hydratedAssets: [{ id: "persisted-image", filename: "persisted.png", status: "READY", content_type: "image/png", media_metadata: {} }],
  });
  const tree = h.render();
  function nodes(node) { if (!node || typeof node !== "object") return []; return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
  const option = nodes(tree).find((node) => node.type === "option" && node.props.value === "persisted-image");
  assert.ok(option);
  assert.equal(option.props.children, "persisted.png");
});

test("late settings preview cannot be accepted for a different profile", async () => {
  const h = setup(); const pending = h.button("Xem trước prompt").props.onClick();
  h.changeSelect("STANDARD", "HIGH"); await h.resolvePreview(pending);
  assert.equal(h.button("Chấp nhận nguyên văn prompt").props.disabled, true);
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
  assert.deepEqual(h.sent, []);
});

test("editing accepted text requires acceptance again", async () => {
  const h = setup(); const pending = h.button("Xem trước prompt").props.onClick(); await h.resolvePreview(pending);
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.edit("different text");
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
});

test("a newly observed source revision disables an already accepted prompt", async () => {
  const h = setup(); const pending = h.button("Xem trước prompt").props.onClick(); await h.resolvePreview(pending);
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.scene.revision++; h.video.revision++; h.render();
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
});

const motionSettings = {
  motion_context: { enabled: true, context_frames: 22, audio_context_frames: 24, continuity: true, keep_tail: false },
  refine: { enabled: false, mode: "refine", upscale_method: "h3_latent", passes: 1, seed_mode: "inherit", aspect_ratio: "follow_director", megapixels: 0, width: 0, height: 0, skip_fl2v: true, enable_latent_chunking: false, enable_tiling: false },
  face_refine: { enabled: false, detector: "face_yolov8m.pt", confidence: 0.35, crop_factor: 2.5, canvas_width: 768, canvas_height: 768, canvas_mode: "manual", select: "largest_face", denoise: 0.4, steps: 8, seed_mode: "inherit", paste_region: "face_only", mask_dilation: 16, feather: 24, colour_match: 1, blend: 1 },
  audio_policy: { mode: "generate", preserve_source_audio: true },
};
const aggregate = {
  workflow_id: "aggregate-only", execution_scope: "aggregate", provider: "minimax_h3_director",
  mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16", required_asset_slots: [],
  max_reference_images: 0, max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0,
  supports_motion_context: true, supports_refine: false, supports_face_refine: false,
  director_settings: [motionSettings],
};

test("Refine and Face Refine remain unavailable when support flags lack qualified settings evidence", () => {
  for (const scope of ["single_scene", "aggregate"]) {
    const h = setup({
      combinations: [{ ...aggregate, execution_scope: scope, supports_refine: true, supports_face_refine: true, director_settings: [] }],
      sceneConfig: scope === "aggregate" ? { motion_context: { enabled: true } } : {},
    });
    assert.equal(checkbox(h, "Tinh chỉnh").props.disabled, true);
    assert.equal(checkbox(h, "Tinh chỉnh khuôn mặt").props.disabled, true);
  }
});

test("upstream source support and unqualified combinations never enable Refine controls", () => {
  const advertised = { ...aggregate, execution_scope: "single_scene", supports_refine: true, supports_face_refine: true };
  const h = setup({ capabilities: {
    available: true, source_capabilities: { supports_refine: true, supports_face_refine: true },
    combinations: [advertised], qualified_capabilities: { combinations: [] },
  } });
  assert.equal(checkbox(h, "Tinh chỉnh").props.disabled, true);
  assert.equal(checkbox(h, "Tinh chỉnh khuôn mặt").props.disabled, true);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
});

test("scope-qualified Refine and Face Refine settings save through aggregate only with exact combined evidence", async () => {
  const combined = { ...motionSettings, refine: { ...motionSettings.refine, enabled: true }, face_refine: { ...motionSettings.face_refine, enabled: true } };
  const evidence = { ...aggregate, supports_refine: true, supports_face_refine: true, director_settings: [combined] };
  const h = setup({ combinations: [evidence], sceneConfig: combined });
  assert.equal(checkbox(h, "Tinh chỉnh").props.disabled, false);
  assert.equal(checkbox(h, "Tinh chỉnh khuôn mặt").props.disabled, false);
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(h.saved[0][1].generation_config.refine, combined.refine);
  assert.deepEqual(h.saved[0][1].generation_config.face_refine, combined.face_refine);
  assert.deepEqual(h.sent, []);
  for (const field of ["refine", "face_refine"]) {
    const changed = { ...combined, [field]: { ...combined[field], ...(field === "refine" ? { passes: 2 } : { confidence: 0.4 }) } };
    const invalid = setup({ combinations: [evidence], sceneConfig: changed });
    assert.equal(invalid.button("Lưu cấu hình cảnh").props.disabled, true);
    await invalid.button("Lưu cấu hình cảnh").props.onClick();
    assert.deepEqual(invalid.saved, []);
    assert.deepEqual(invalid.sent, []);
  }
  const direct = setup({ combinations: [evidence] });
  assert.equal(checkbox(direct, "Tinh chỉnh").props.disabled, true);
  assert.equal(checkbox(direct, "Tinh chỉnh khuôn mặt").props.disabled, true);
  const separateEvidence = setup({
    combinations: [{ ...evidence, director_settings: [
      { ...motionSettings, refine: combined.refine },
      { ...motionSettings, face_refine: combined.face_refine },
    ] }], sceneConfig: combined,
  });
  assert.equal(separateEvidence.button("Lưu cấu hình cảnh").props.disabled, true);
  await separateEvidence.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(separateEvidence.saved, []);
});

test("feature support flags remain required even when settings examples enable Refine", () => {
  const examples = { ...motionSettings, refine: { ...motionSettings.refine, enabled: true }, face_refine: { ...motionSettings.face_refine, enabled: true } };
  const h = setup({ combinations: [{ ...aggregate, director_settings: [examples] }], sceneConfig: examples });
  assert.equal(checkbox(h, "Tinh chỉnh").props.disabled, false); // Stored enabled values can be disabled.
  assert.equal(checkbox(h, "Tinh chỉnh khuôn mặt").props.disabled, false);
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, true);
  assert.equal(nodes(h.render()).some(node => node.type === "option" && String(node.props.children).includes("Tinh chỉnh")), false);
});
function nodes(node) { return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
function checkbox(h, label) {
  const row = nodes(h.render()).find((node) => node.type === "label" && React.Children.toArray(node.props.children).some(child => typeof child === "string" && child.trim() === label));
  assert.ok(row, label);
  return nodes(row).find((node) => node.type === "input");
}

test("revoked saved features can be disabled and base config saved", async () => {
  const h = setup({ combinations: [], sceneConfig: {
    motion_context: { enabled: true }, refine: { enabled: true }, face_refine: { enabled: true },
  } });
  for (const label of ["Ngữ cảnh chuyển động", "Tinh chỉnh", "Tinh chỉnh khuôn mặt"]) {
    assert.equal(checkbox(h, label).props.disabled, false);
    checkbox(h, label).props.onChange({ target: { checked: false } });
    h.render();
    assert.equal(checkbox(h, label).props.disabled, true);
  }
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.equal(h.saved.length, 1);
  assert.equal(h.saved[0][1].generation_config.refine.enabled, false);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
});

for (const remaining of ["Tinh chỉnh", "Tinh chỉnh khuôn mặt"]) test(`disabling Motion Context cannot save unqualified remaining ${remaining}`, async () => {
  const h = setup({ combinations: [], sceneConfig: {
    motion_context: { enabled: true }, refine: { enabled: true }, face_refine: { enabled: true },
  } });
  checkbox(h, "Ngữ cảnh chuyển động").props.onChange({ target: { checked: false } });
  h.render();
  const other = remaining === "Tinh chỉnh" ? "Tinh chỉnh khuôn mặt" : "Tinh chỉnh";
  checkbox(h, other).props.onChange({ target: { checked: false } });
  h.render();
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, true);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(h.saved, []);
  checkbox(h, remaining).props.onChange({ target: { checked: false } });
  h.render();
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.equal(h.saved.length, 1);
});

test("Refine shows qualified megapixel preset and no editable dimensions", () => {
  const preset = { ...motionSettings, refine: { ...motionSettings.refine, enabled: true, megapixels: 2 } };
  const h = setup({ combinations: [{ ...aggregate, supports_refine: true, director_settings: [preset] }], sceneConfig: preset });
  const all = nodes(h.render());
  assert.ok(all.some(node => node.type === "option" && String(node.props.children).includes("2 MP")));
  assert.equal(all.some(node => node.type === "label" && /(Refine (width|height)|(?:Chiều rộng|Chiều cao) tinh chỉnh)/.test(String(node.props.children))), false);
  assert.equal(all.some(node => node.type === "select" && node.props.value === "h3_latent"), false);
});

test("revoked Refine can be removed from a continuity group and base config saved", async () => {
  const h = setup({ combinations: [], sceneConfig: { refine: { enabled: true } } });
  h.video.scenes = [h.scene, { id: "next", enabled: true, scene_order: 1, spec: { continuity: "CONTINUOUS" } }];
  h.render();
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, true);
  checkbox(h, "Tinh chỉnh").props.onChange({ target: { checked: false } });
  h.render();
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.equal(h.saved[0][1].generation_config.refine.enabled, false);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
});

test("qualified preset applies exact Refine megapixels and combined settings on save", async () => {
  const preset = { ...motionSettings, refine: { ...motionSettings.refine, enabled: true, megapixels: 2 } };
  const h = setup({ combinations: [{ ...aggregate, supports_refine: true, director_settings: [preset] }], sceneConfig: motionSettings });
  const select = nodes(h.render()).find(n => n.type === "select" && nodes(n).some(child => child.type === "option" && String(child.props.children).includes("2 MP")));
  select.props.onChange({ target: { value: "0" } });
  h.render();
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(h.saved[0][1].generation_config.refine, preset.refine);
  assert.deepEqual(h.saved[0][1].generation_config.motion_context, preset.motion_context);
});

test("aggregate-only Motion Context can be saved but cannot preview or submit standalone", async () => {
  const h = setup({ combinations: [aggregate], sceneConfig: { motion_context: { enabled: true } } });
  assert.equal(checkbox(h, "Ngữ cảnh chuyển động").props.disabled, false);
  assert.equal(checkbox(h, "Tinh chỉnh").props.disabled, true);
  assert.equal(checkbox(h, "Tinh chỉnh khuôn mặt").props.disabled, true);
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.equal(h.saved[0][1].generation_config.motion_context.enabled, true);
  assert.equal(h.saved[0][1].generation_config.workflow_id, undefined);
  assert.deepEqual(h.sent, []);
});

test("Motion Context requires aggregate support and exact settings evidence; Refine stays fail closed", async () => {
  const h = setup({ combinations: [{ ...aggregate, director_settings: [] }], sceneConfig: { motion_context: { enabled: true }, refine: { enabled: true } } });
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, true);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(h.saved, []);
  assert.deepEqual(h.sent, []);
  assert.equal(checkbox(setup(), "Ngữ cảnh chuyển động").props.disabled, true);
});

for (const member of [0, 1, 2]) test(`editor blocks accepted standalone prompt when scene becomes chain member ${member}`, async () => {
  const h = setup();
  const pending = h.button("Xem trước prompt").props.onClick(); await h.resolvePreview(pending);
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.scene.scene_order = member;
  h.scene.spec = { continuity: member === 0 ? "CUT" : "CONTINUOUS" };
  h.video.scenes = [0, 1, 2].map((index) => index === member ? h.scene : {
    id: `other-${index}`, enabled: true, scene_order: index, spec: { continuity: index === 0 ? "CUT" : "CONTINUOUS" },
  });
  h.render();
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
  await h.button("Tạo video với prompt đã chấp nhận").props.onClick();
  assert.deepEqual(h.sent, []);
});

function historicalGeneration(snapshot = {}) {
  return { id: "historical", scene_id: "scene", mode: "t2v", workflow_id: "retired-workflow", input_snapshot: {
    mode: "t2v", requested_quality_profile: "STANDARD", requested_aspect_ratio: "9:16",
    duration_seconds: 5, seed_policy: "FIXED", seed: 123,
    requested_width: 480, requested_height: 864, frames: 124, steps: 8, cfg: 1, fps: 24,
    runtime_profile: { steps: 8 }, workflow_id: "retired-workflow",
    director_execution: { canvas: { width: 480, height: 864 }, cfg: 1, fps: 24, frames: 124,
      audio_policy: { mode: "generate", preserve_source_audio: true } }, ...snapshot,
  } };
}

async function reuseViaPage(parent) {
  const { pageHarness } = require("./load-typescript.cjs");
  const scene = { id: "scene", enabled: true, scene_order: 0, revision: 2, spec: { continuity: "CUT" },
    duration_seconds: 8, generation_config: { frames: 124, width: 480, height: 864, cfg: 1, steps: 8, fps: 24 } };
  const video = { id: "video", revision: 4, config: {}, scenes: [scene] };
  const patches = [];
  const oldConfig = scene.generation_config;
  const harness = pageHarness(video, { "@/lib/api/scenes": { patchScene: async (...args) => {
    patches.push(args); scene.generation_config = args[1].generation_config; return scene;
  } } });
  const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
  harness.render(page, { videoId: video.id });
  const reuse = harness.mutations.find(item => item.mutationFn.toString().includes("reuseGenerationSettings"));
  assert.ok(reuse, "actual page Reuse action");
  const History = loadTypeScript("components/generation/history-details.tsx", {
    "@tanstack/react-query": { useQuery: () => ({ data: undefined }) },
  }).GenerationHistoryDetails;
  const tree = History({ generation: parent, scene, scenes: video.scenes, busy: false,
    onRegenerate: () => {}, onVariation: () => {}, onReuse: () => reuse.mutationFn(parent) });
  const reuseButton = nodes(tree).find(node => node.props?.children === "Dùng lại cấu hình cho cảnh");
  assert.ok(reuseButton);
  assert.equal(reuseButton.props.disabled, false);
  await reuseButton.props.onClick();
  assert.notEqual(scene.generation_config, oldConfig);
  for (const field of ["frames", "width", "height", "cfg", "steps", "fps"]) {
    assert.equal(Object.hasOwn(scene.generation_config, field), false, `Reuse replaces stale ${field}`);
  }
  assert.equal(patches[0][0], "scene");
  assert.equal(patches[0][2], 2);
  return scene.generation_config;
}

async function submitEditor(h) {
  assert.equal(h.button("Xem trước prompt").props.disabled, false);
  const pending = h.button("Xem trước prompt").props.onClick();
  await h.resolvePreview(pending);
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, false);
  await h.button("Tạo video với prompt đã chấp nhận").props.onClick();
  return h.sent[0];
}

for (const change of ["duration", "quality", "canvas"]) test(`History Reuse through page and editor drops frozen overrides after ${change} changes`, async () => {
  const parent = historicalGeneration();
  const before = JSON.stringify(parent);
  const config = await reuseViaPage(parent);
  const current = { workflow_id: "current-qualified", provider: "minimax_h3_director", execution_scope: "single_scene",
    mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16", resolved_width: 480, resolved_height: 864,
    steps: 12, fps: 24, cfg: 3, required_asset_slots: [], max_reference_images: 0, max_reference_videos: 0,
    max_reference_audio: 0, max_total_reference_files: 0 };
  const h = setup({ sceneConfig: config, combinations: [current, { ...current, quality_profile: "HIGH" },
    { ...current, aspect_ratio: "16:9", resolved_width: 1536, resolved_height: 864 }] });
  h.scene.duration_seconds = 8;
  if (change === "quality") h.changeSelect("STANDARD", "HIGH");
  if (change === "canvas") h.changeSelect("9:16", "16:9");
  const payload = await submitEditor(h);
  for (const request of [config, h.previews[0].payload, payload]) {
    for (const field of ["frames", "width", "height", "cfg", "steps", "fps", "runtime_profile", "director_execution", "director_execution_spec"]) {
      assert.equal(Object.hasOwn(request, field), false, `${field} must resolve from the current scene/profile`);
    }
  }
  assert.equal(payload.workflow_id, "current-qualified");
  assert.equal(payload.seed, 123);
  assert.equal(payload.seed_policy, "FIXED");
  assert.equal(payload.quality_profile, change === "quality" ? "HIGH" : "STANDARD");
  assert.equal(payload.aspect_ratio, change === "canvas" ? "16:9" : "9:16");
  assert.equal(JSON.stringify(parent), before);
});

for (const mode of ["i2v", "i2v_last", "i2v_first_last", "r2v", "v2v", "rv2v"]) test(`History Reuse editor preserves ${mode} bindings and ordered media without pinning random seed`, async () => {
  const bindings = mode === "i2v" ? [{ id: "first", role: "FIRST_FRAME" }]
    : mode === "i2v_last" ? [{ id: "last", role: "LAST_FRAME" }]
    : mode === "i2v_first_last" ? [{ id: "first", role: "FIRST_FRAME" }, { id: "last", role: "LAST_FRAME" }]
    : [ ...(mode !== "r2v" ? [{ id: "source", role: "SOURCE_VIDEO" }] : []),
      ...(mode !== "v2v" ? [
        { id: "image-2", role: "REFERENCE_IMAGE", order_index: 1 }, { id: "image-1", role: "REFERENCE_IMAGE", order_index: 0 },
        { id: "video-2", role: "REFERENCE_VIDEO", order_index: 1 }, { id: "video-1", role: "REFERENCE_VIDEO", order_index: 0 },
        { id: "audio-2", role: "REFERENCE_AUDIO", order_index: 1 }, { id: "audio-1", role: "REFERENCE_AUDIO", order_index: 0 },
      ] : []) ];
  const parent = historicalGeneration({ mode, seed_policy: "RANDOM", assets: bindings });
  const before = JSON.stringify(parent);
  const sceneConfig = await reuseViaPage(parent);
  const assets = bindings.map(({ id, role }) => {
    const kind = role.includes("VIDEO") ? "video" : role.includes("AUDIO") ? "audio" : "image";
    return { id, filename: id, status: "READY", content_type: `${kind}/test`, media_metadata: { [`${kind}_duration_seconds`]: 5, fps: 24 } };
  });
  const cap = { workflow_id: `current-${mode}`, mode, execution_scope: "single_scene", quality_profile: "STANDARD", aspect_ratio: "9:16",
    required_asset_slots: [], max_reference_images: 2, max_reference_videos: 2, max_reference_audio: 2, max_total_reference_files: 6,
    clip_min_seconds: 2, clip_max_seconds: 15, category_total_max_seconds: 15, reference_video_fps: 24 };
  const h = setup({ sceneConfig, combinations: [cap], hydratedAssets: assets });
  const payload = await submitEditor(h);
  assert.equal(payload.mode, mode);
  assert.equal(payload.seed_policy, "RANDOM"); assert.equal(payload.seed, null);
  assert.equal(payload.first_frame_asset_id, mode.includes("first") || mode === "i2v" ? "first" : null);
  assert.equal(payload.last_frame_asset_id, mode.includes("last") ? "last" : null);
  assert.equal(payload.source_video_asset_id, ["v2v", "rv2v"].includes(mode) ? "source" : null);
  for (const kind of ["image", "video", "audio"]) {
    assert.deepEqual(payload[`reference_${kind}_asset_ids`], ["r2v", "rv2v"].includes(mode) ? [`${kind}-1`, `${kind}-2`] : []);
  }
  assert.equal(JSON.stringify(parent), before);
});

test("History Reuse preserves qualified Refine and Face Refine and blocks revoked support", async () => {
  const options = { ...motionSettings, motion_context: { ...motionSettings.motion_context, enabled: false },
    refine: { ...motionSettings.refine, enabled: true }, face_refine: { ...motionSettings.face_refine, enabled: true } };
  const parent = historicalGeneration({ director_execution: { canvas: { width: 480, height: 864 }, cfg: 1, fps: 24, frames: 124, ...options } });
  const before = JSON.stringify(parent);
  const sceneConfig = await reuseViaPage(parent);
  const cap = { ...aggregate, workflow_id: "qualified-refine", execution_scope: "single_scene", supports_refine: true,
    supports_face_refine: true, director_settings: [options] };
  const h = setup({ sceneConfig, combinations: [cap] });
  const payload = await submitEditor(h);
  assert.deepEqual(payload.refine, options.refine);
  assert.deepEqual(payload.face_refine, options.face_refine);
  assert.deepEqual(payload.audio_policy, options.audio_policy);
  const revoked = setup({ sceneConfig, combinations: [{ ...cap, supports_refine: false, supports_face_refine: false }] });
  assert.equal(revoked.button("Xem trước prompt").props.disabled, true);
  // A disabled native button cannot dispatch the preview action.
  assert.deepEqual(revoked.previews, []);
  assert.deepEqual(revoked.sent, []);
  assert.equal(JSON.stringify(parent), before);
  sceneConfig.refine.passes = 2;
  assert.equal(JSON.stringify(parent), before);
});

test("History Reuse retains aggregate Motion Context for save and blocks direct generation", async () => {
  const parent = historicalGeneration({ director_execution: { canvas: { width: 480, height: 864 }, cfg: 1, fps: 24, frames: 124, ...motionSettings } });
  const sceneConfig = await reuseViaPage(parent);
  const h = setup({ sceneConfig, combinations: [aggregate] });
  assert.equal(h.button("Lưu cấu hình cảnh").props.disabled, false);
  await h.button("Lưu cấu hình cảnh").props.onClick();
  assert.deepEqual(h.saved[0][1].generation_config.motion_context, motionSettings.motion_context);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  // Aggregate settings keep the native preview button disabled.
  assert.deepEqual(h.previews, []);
  assert.deepEqual(h.sent, []);
});

test("History Reuse of ambiguous Custom canvas requires fresh dimensions before preview", async () => {
  const parent = historicalGeneration({ requested_aspect_ratio: "Custom" });
  const sceneConfig = await reuseViaPage(parent);
  const h = setup({ sceneConfig, combinations: [{ workflow_id: "custom-qualified", mode: "t2v", execution_scope: "single_scene",
    quality_profile: "STANDARD", aspect_ratio: "Custom", required_asset_slots: [], max_reference_images: 0,
    max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0 }] });
  assert.equal(sceneConfig.aspect_ratio, "Custom");
  assert.equal(Object.hasOwn(sceneConfig, "width"), false);
  assert.equal(Object.hasOwn(sceneConfig, "height"), false);
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  assert.deepEqual(h.previews, []);
  assert.deepEqual(h.sent, []);
});

test("ordinary explicit Custom editor values remain intact without History Reuse", async () => {
  const explicit = { mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "Custom", width: 640, height: 640,
    cfg: 3, steps: 12, fps: 24, frames: 192, seed_policy: "FIXED", seed: 42 };
  const h = setup({ sceneConfig: explicit, combinations: [{ workflow_id: "custom-qualified", mode: "t2v", execution_scope: "single_scene",
    quality_profile: "STANDARD", aspect_ratio: "Custom", resolved_width: 640, resolved_height: 640, steps: 12, fps: 24,
    required_asset_slots: [], max_reference_images: 0, max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0 }] });
  h.scene.duration_seconds = 8;
  const payload = await submitEditor(h);
  for (const field of ["width", "height", "cfg", "steps", "fps", "frames", "seed"]) {
    assert.equal(payload[field], explicit[field]);
    assert.equal(h.previews[0].payload[field], explicit[field]);
  }
  await h.button("Lưu cấu hình cảnh").props.onClick();
  for (const field of ["width", "height", "cfg", "steps", "fps", "frames"]) {
    assert.equal(h.saved[0][1].generation_config[field], explicit[field]);
  }
});
