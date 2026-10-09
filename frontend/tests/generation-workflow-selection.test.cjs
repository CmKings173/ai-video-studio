const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

const combination = (workflow_id, extra = {}) => ({
  workflow_id, workflow_version: `${workflow_id}-v1`, execution_scope: "single_scene",
  mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16",
  resolved_width: 480, resolved_height: 864, fps: 24, required_asset_slots: [],
  max_reference_images: 0, max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0,
  ...extra,
});
const first = combination("test-first");
const second = combination("test-second", { resolved_width: 720, resolved_height: 1280 });
const sameSizeVariant = combination("test-first-variant", { workflow_version: "alternate-v1" });
const highSameSize = combination("test-high-same-size", { quality_profile: "HIGH" });
const input = { mode: "AUTO", quality_profile: "STANDARD", aspect_ratio: "9:16" };
const capabilities = { available: true, qualified_capabilities: [first, second, sameSizeVariant, highSameSize] };

test("two matching workflows remain discoverable and an explicit second choice resolves the second", () => {
  const api = loadTypeScript("lib/generation/capabilities.ts");
  assert.deepEqual(api.matchingQualifiedCapabilities(capabilities, input, "single_scene"), [first, second, sameSizeVariant]);
  assert.equal(api.singleSceneCapability(capabilities, input), first);
  assert.equal(api.singleSceneCapability(capabilities, { ...input, workflow_id: second.workflow_id }), second);
  assert.deepEqual(api.matchingQualifiedCapabilities(capabilities, input, "single_scene").map(item => item.workflow_id), [first.workflow_id, second.workflow_id, sameSizeVariant.workflow_id]);
  assert.deepEqual(api.matchingQualifiedCapabilities(capabilities, { ...input, quality_profile: "HIGH" }, "single_scene").map(item => item.workflow_id), [highSameSize.workflow_id]);
  const custom = combination("custom", { aspect_ratio: "Custom", resolved_width: 640, resolved_height: 640 });
  assert.equal(api.singleSceneCapability({ available: true, qualified_capabilities: [custom] }, { mode: "AUTO", quality_profile: "STANDARD", aspect_ratio: "Custom", width: 640, height: 640 }), custom);
  assert.equal(api.singleSceneCapability({ available: true, qualified_capabilities: [custom] }, { mode: "AUTO", quality_profile: "STANDARD", aspect_ratio: "Custom", width: 672, height: 640 }), null);
});

test("unknown workflow IDs and incompatible contexts fail closed without a scope fallback", () => {
  const api = loadTypeScript("lib/generation/capabilities.ts");
  assert.equal(api.singleSceneCapability(capabilities, { ...input, workflow_id: "unknown" }), null);
  for (const change of [{ mode: "i2v" }, { quality_profile: "HIGH" }, { aspect_ratio: "16:9" }]) {
    assert.equal(api.singleSceneCapability(capabilities, { ...input, workflow_id: second.workflow_id, ...change }), null);
  }
  const aggregate = combination("aggregate-only", { execution_scope: "aggregate" });
  const caps = { available: true, qualified_capabilities: [first, aggregate] };
  assert.equal(api.singleSceneCapability(caps, { ...input, workflow_id: aggregate.workflow_id }), null);
  assert.equal(api.aggregateCapability(caps, { ...input, workflow_id: first.workflow_id }), null);
  assert.equal(api.aggregateCapability(caps, { ...input, workflow_id: aggregate.workflow_id }), aggregate);
  assert.deepEqual(api.matchingQualifiedCapabilities({ ...caps, available: false }, input, "single_scene"), []);
});

function nodes(node) {
  return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}
function harness(locale = "vi", config = {}) {
  let cursor = 0, tree;
  const states = [], sent = [], previews = [], saved = [], effectDeps = new Map();
  const scene = { id: "scene", revision: 2, scene_order: 0, duration_seconds: 5, enabled: true, spec: { continuity: "CUT" }, generation_config: config };
  const scenes = [scene];
  const video = { id: "video", revision: 4, project_id: "project", aspect_ratio: "9:16" };
  const caps = { available: true, qualified_capabilities: [first, second, sameSizeVariant, highSameSize,
    combination("wrong-scope", { execution_scope: "aggregate" }),
    combination("high", { quality_profile: "HIGH", resolved_width: 1080, resolved_height: 1920 }),
    combination("wrong-mode", { mode: "i2v" }),
    combination("wrong-ratio", { aspect_ratio: "16:9" }),
    combination("custom", { aspect_ratio: "Custom", resolved_width: 640, resolved_height: 640 }),
  ] };
  const t = (vi, en) => locale === "en" ? en : vi;
  const Editor = loadTypeScript("components/generation/generation-editor.tsx", {
    react: { ...React, useEffect(effect, deps) {
      const key = 0;
      const previous = effectDeps.get(key);
      if (!previous || deps.some((value, index) => value !== previous[index])) { effect(); effectDeps.set(key, [...deps]); }
    }, useState(initial) {
      const index = cursor++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }];
    } },
    "@/lib/i18n": { useI18n: () => ({ locale, t }), translateText: t },
    "@tanstack/react-query": {
      useQuery: () => ({ data: caps }), useQueries: () => [],
      useQueryClient: () => ({ invalidateQueries: async () => {} }),
      useInfiniteQuery: () => ({ data: { pages: [{ items: [] }] } }),
    },
    "@/lib/api/scenes": { patchScene: async (...args) => saved.push(args) },
    "@/lib/api/generations": {
      generationCapabilities: async () => caps,
      previewPrompt: async (id, payload) => { previews.push(payload); return { execution_prompt: "User prompt", scene_revision: scene.revision, video_revision: video.revision, warnings: [] }; },
    },
  }).GenerationEditor;
  const render = () => {
    for (let pass = 0; pass < 2; pass++) { cursor = 0; tree = Editor({ scene, scenes, video, onClose() {}, onSubmit: async payload => sent.push(payload), isLoading: false }); }
    return tree;
  };
  const all = () => nodes(tree);
  const button = label => { const node = all().find(node => node.props?.children === label); assert.ok(node, label); return node; };
  const workflow = () => all().find(node => node.type === "select" && nodes(node).some(child => child.type === "option" && child.props.children === t("Tự động chọn workflow phù hợp", "Automatically select a compatible workflow")));
  const resolution = () => all().find(node => node.type === "select" && nodes(node).some(child => child.type === "option" && child.props.children === t("Tự động chọn độ phân giải phù hợp", "Automatically select a compatible resolution")));
  const choose = value => { workflow().props.onChange({ target: { value } }); render(); };
  render();
  return { render, all, button, workflow, resolution, choose, sent, previews, saved, scene, scenes, caps, t };
}

for (const locale of ["vi", "en"]) test(`editor ${locale}: compatible options, numeric resolution, explicit submission and request-only persistence`, async () => {
  const h = harness(locale);
  assert.equal(h.workflow().props.value, "");
  assert.deepEqual(nodes(h.resolution()).filter(node => node.type === "option").map(node => node.props.value), ["", "480x864", "720x1280"]);
  const quality = h.all().find(node => node.type === "label" && React.Children.toArray(node.props.children)[0] === h.t("Cấu hình chất lượng", "Quality profile"));
  assert.ok(quality);
  assert.equal(nodes(quality).find(node => node.type === "option" && node.props.value === "STANDARD").props.children, h.t("Tiêu chuẩn", "Standard"));
  const resolutionLabels = nodes(h.resolution()).filter(node => node.type === "option").map(node => React.Children.toArray(node.props.children).join(""));
  assert.ok(resolutionLabels.some(label => label.includes("480×864") && label.endsWith("px")));
  h.resolution().props.onChange({ target: { value: "720x1280" } }); h.render();
  await h.button(h.t("Xem trước prompt", "Preview prompt")).props.onClick(); h.render();
  assert.equal(h.previews[0].workflow_id, second.workflow_id);
  await h.button(h.t("Chấp nhận nguyên văn prompt", "Accept exact prompt")).props.onClick(); h.render();
  await h.button(h.t("Tạo video với prompt đã chấp nhận", "Generate video with accepted prompt")).props.onClick();
  assert.equal(h.sent[0].workflow_id, second.workflow_id);
  assert.equal(h.sent[0].execution_prompt, "User prompt");
  await h.button(h.t("Lưu cấu hình cảnh", "Save scene settings")).props.onClick();
  assert.equal(Object.hasOwn(h.saved[0][1].generation_config, "workflow_id"), false);
});

test("automatic resolution defaults to the first qualified workflow and advanced workflow pin stays within selected pixels", async () => {
  const h = harness();
  await h.button("Xem trước prompt").props.onClick(); h.render();
  assert.equal(h.previews[0].workflow_id, first.workflow_id);
  assert.ok(nodes(h.workflow()).some(node => node.type === "option" && node.props.value === sameSizeVariant.workflow_id));
  h.resolution().props.onChange({ target: { value: "480x864" } }); h.render();
  h.choose(sameSizeVariant.workflow_id);
  await h.button("Xem trước prompt").props.onClick(); h.render();
  assert.equal(h.previews.at(-1).workflow_id, sameSizeVariant.workflow_id);
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  await h.button("Tạo video với prompt đã chấp nhận").props.onClick();
  assert.equal(h.sent[0].workflow_id, sameSizeVariant.workflow_id);
});

test("quality profile collisions keep their own pixel-resolution choices", () => {
  const h = harness();
  const profile = h.all().find(node => node.type === "select" && nodes(node).some(child => child.type === "option" && child.props.value === "STANDARD"));
  profile.props.onChange({ target: { value: "HIGH" } }); h.render();
  assert.deepEqual(nodes(h.resolution()).filter(node => node.type === "option").map(node => node.props.value), ["", "480x864", "1080x1920"]);
  assert.ok(nodes(h.workflow()).some(node => node.type === "option" && node.props.value === "test-high-same-size"));
  assert.ok(!nodes(h.workflow()).some(node => node.type === "option" && node.props.value === second.workflow_id));
});

test("editor rejects unknown workflow IDs and invalidates accepted prompts on selection and context changes", async () => {
  const h = harness();
  await h.button("Xem trước prompt").props.onClick(); h.render();
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.choose(second.workflow_id);
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
  await h.button("Xem trước prompt").props.onClick(); h.render();
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  const profile = h.all().find(node => node.type === "select" && node.props.value === "STANDARD");
  profile.props.onChange({ target: { value: "HIGH" } }); h.render();
  assert.equal(h.workflow().props.value, "");
  assert.equal(h.button("Xem trước prompt").props.disabled, false);
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
  h.choose("unknown");
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  assert.deepEqual(h.sent, []);
});

test("custom dimensions retain numeric limits and fail validation outside their permitted range", () => {
  const h = harness("en", { aspect_ratio: "Custom", width: 640, height: 640 });
  const fields = h.all().filter(node => node.type === "input" && node.props.min === "32");
  assert.equal(fields.length, 2);
  for (const field of fields) { assert.equal(field.props.max, "8192"); assert.equal(field.props.step, "32"); }
  assert.equal(h.button("Preview prompt").props.disabled, false);
  fields[0].props.onChange({ target: { value: "33" } }); h.render();
  assert.equal(h.button("Preview prompt").props.disabled, true);
  assert.ok(h.all().some(node => node.type === "li" && node.props.children.includes("divisible by 32")));
});

test("custom dimensions inside the numeric limits still fail if they differ from every qualified canvas", () => {
  const h = harness("en", { aspect_ratio: "Custom", width: 672, height: 640 });
  assert.equal(h.button("Preview prompt").props.disabled, true);
  assert.ok(h.all().some(node => node.type === "li" && node.props.children.includes("must match a verified resolution: 640×640")));
  assert.deepEqual(h.previews, []);
  const { generationInputProblems } = loadTypeScript("lib/generation/capabilities.ts", { "@/lib/i18n": { translateText: (_vi, en) => en } });
  assert.ok(generationInputProblems({ aspect_ratio: "Custom", width: 672, height: 640 }, null, [], [combination("canvas", { aspect_ratio: "Custom", resolved_width: 640, resolved_height: 640 })])
    .includes("Custom dimensions must match a verified resolution: 640×640."));
});

for (const [field, value] of [["AUTO", "i2v"], ["9:16", "16:9"]]) test(`changing ${field} clears explicit workflow choice and prompt acceptance`, async () => {
  const h = harness(); h.choose(second.workflow_id);
  await h.button("Xem trước prompt").props.onClick(); h.render();
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.all().find(node => node.type === "select" && node.props.value === field).props.onChange({ target: { value } }); h.render();
  assert.equal(h.workflow().props.value, "");
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
});

test("changing custom dimensions clears explicit workflow and the accepted prompt", async () => {
  const h = harness("vi", { aspect_ratio: "Custom", width: 640, height: 640 }); h.resolution().props.onChange({ target: { value: "640x640" } }); h.render();
  await h.button("Xem trước prompt").props.onClick(); h.render();
  await h.button("Chấp nhận nguyên văn prompt").props.onClick(); h.render();
  h.all().find(node => node.type === "input" && node.props.min === "32").props.onChange({ target: { value: "672" } }); h.render();
  assert.equal(h.workflow().props.value, "");
  assert.equal(h.button("Tạo video với prompt đã chấp nhận").props.disabled, true);
});

test("a scene moving into aggregate scope can always clear a stale single-scene workflow", () => {
  const h = harness(); h.choose(second.workflow_id);
  h.scene.spec.continuity = "CONTINUOUS";
  h.scene.scene_order = 1;
  h.scenes.unshift({ ...h.scene, id: "previous", scene_order: 0, spec: { continuity: "CUT" } });
  h.render();
  assert.equal(h.button("Xem trước prompt").props.disabled, true);
  assert.equal(h.workflow().props.value, "");
  assert.ok(!nodes(h.workflow()).some(node => node.type === "option" && node.props.value === second.workflow_id));
  assert.ok(nodes(h.workflow()).some(node => node.type === "option" && node.props.value === "wrong-scope" && node.props.disabled));
  assert.notEqual(h.workflow().props.disabled, true);
  h.choose("");
  assert.equal(h.workflow().props.value, "");
});

for (const locale of ["vi", "en"]) test(`remaining generation components render ${locale} labels, ARIA and fallbacks`, () => {
  const t = (vi, en) => locale === "en" ? en : vi;
  const mocks = { "@/lib/i18n": { useI18n: () => ({ locale, t }), translateText: t },
    "@tanstack/react-query": { useQuery: () => ({}) } };
  const refs = loadTypeScript("components/generation/ordered-references.tsx", mocks).OrderedReferences({ label: t("Ảnh tham chiếu", "Reference images"), tag: "Image", assets: [], selected: ["user-id"], limit: 0, onChange() {} });
  assert.ok(nodes(refs).some(node => node.props?.["aria-label"] === t("Bỏ Ảnh tham chiếu 1", "Remove Reference images 1")));
  assert.ok(nodes(refs).some(node => node.props?.children === t("Chọn tài nguyên theo thứ tự…", "Select assets in order…")));
  const scene = { id: "scene", enabled: true, scene_order: 0, duration_seconds: 5, spec: { continuity: "CUT" }, generation_config: {} };
  const batch = loadTypeScript("components/generation/batch-summary.tsx", mocks).BatchSummary({ video: { scenes: [scene], aspect_ratio: "9:16" } });
  assert.ok(nodes(batch).some(node => node.type === "summary" && node.props.children === t("Tạo toàn bộ · 1 cảnh cần tạo", "Generate all · 1 scenes to generate")));
  assert.ok(nodes(batch).some(node => node.type === "td" && node.props.children === t("Ngẫu nhiên", "Random")));
  const history = loadTypeScript("components/generation/history-details.tsx", mocks).GenerationHistoryDetails({ generation: { input_snapshot: {}, status: "FAILED", operation: "ORIGINAL", mode: "t2v" }, scene, scenes: [scene], busy: false, onRegenerate() {}, onVariation() {}, onReuse() {} });
  assert.ok(nodes(history).some(node => node.props?.children === t("Dùng lại cấu hình cho cảnh", "Reuse settings for scene")));
  assert.ok(nodes(history).some(node => node.type === "pre" && node.props.children === t("Chưa có dữ liệu", "No data")));
});

test("validation uses translateText for English dynamic reference errors", () => {
  const { generationInputProblems } = loadTypeScript("lib/generation/capabilities.ts", { "@/lib/i18n": { translateText: (_vi, en) => en } });
  const problems = generationInputProblems({ reference_image_asset_ids: ["a", "a"] }, combination("test", { mode: "r2v", required_asset_slots: ["REFERENCE_IMAGE_1"] }), []);
  assert.ok(problems.includes("Workflow supports at most 0 references."));
  assert.ok(problems.includes("Workflow supports at most 0 image references."));
  assert.ok(problems.includes("Duplicate image references."));
  assert.ok(problems.includes("This graph requires exactly 1 image references."));
  assert.ok(problems.every(message => !/[à-ỹ]/.test(message)));
});
