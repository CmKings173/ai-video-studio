const assert = require("node:assert/strict");
const test = require("node:test");
const { createHash } = require("node:crypto");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

const hash = (value) => createHash("sha256").update(value).digest("hex");

// Bind preview identity to the selected capability and the actual request settings.
function resolvedConfiguration(item, payload = {}) {
  return {
    runnable: true, requested_mode: payload.mode ?? "AUTO", resolved_mode: item.mode,
    workflow_id: item.workflow_id, workflow_version: item.workflow_version,
    workflow_hash: item.workflow_hash, profile_hash: `profile-${item.workflow_id}`,
    configuration_hash: hash(JSON.stringify(payload)),
    execution_scope: item.execution_scope, quality_profile: item.quality_profile,
    aspect_ratio: item.aspect_ratio, native_width: item.resolved_width,
    native_height: item.resolved_height, output_width: item.resolved_width,
    output_height: item.resolved_height, seed_policy: payload.seed_policy ?? "RANDOM",
    fixed_seed_or_null: payload.seed_policy === "FIXED" ? payload.seed : null,
  };
}

function nodes(node) {
  return !node || typeof node !== "object"
    ? []
    : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function capability(mode, workflowId = mode, overrides = {}) {
  const item = {
    workflow_id: workflowId,
    workflow_hash: `workflow-${workflowId}`,
    workflow_version: `${workflowId}-v1`,
    mode,
    execution_scope: "single_scene",
    quality_profile: "STANDARD",
    aspect_ratio: "9:16",
    resolved_width: 480,
    resolved_height: 864,
    steps: 8,
    fps: 24,
    required_asset_slots: [],
    max_reference_images: 4,
    max_reference_videos: 2,
    max_reference_audio: 2,
    max_total_reference_files: 8,
    clip_min_seconds: 2,
    clip_max_seconds: 15,
    category_total_max_seconds: 15,
    reference_video_fps: 24,
    audio_requires_visual_reference: true,
    ...overrides,
  };
  return { ...item, resolved_configuration: resolvedConfiguration(item) };
}

function setup({ combinations, sourceTasks = [], assets = [] }) {
  let cursor = 0;
  let tree;
  let caps = {
    available: true,
    qualified_capabilities: combinations,
    source_capabilities: { tasks: sourceTasks },
  };
  const states = [];
  const previews = [];
  const submitted = [];
  const scene = {
    id: "scene-1",
    revision: 2,
    scene_order: 0,
    duration_seconds: 5,
    enabled: true,
    spec: { continuity: "CUT" },
    generation_config: {},
  };
  const video = { id: "video-1", revision: 3, project_id: "project-1", aspect_ratio: "9:16" };
  const translate = (_vi, en) => en;
  const Editor = loadTypeScript("components/generation/generation-editor.tsx", {
    react: {
      ...React,
      useState(initial) {
        const index = cursor++;
        if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
        return [states[index], value => {
          states[index] = typeof value === "function" ? value(states[index]) : value;
        }];
      },
    },
    "@/lib/i18n": { useI18n: () => ({ locale: "en", t: translate }), translateText: translate },
    "@tanstack/react-query": {
      useQuery: () => ({ data: caps, isLoading: false }),
      useQueries: () => [],
      useQueryClient: () => ({ fetchQuery: async ({ queryFn }) => queryFn(), invalidateQueries: async () => {} }),
      useInfiniteQuery: () => ({ data: { pages: [{ items: assets }] } }),
    },
    "@/lib/api/assets": { getAsset: async () => undefined, listAssets: async () => ({ items: assets }) },
    "@/lib/api/generations": {
      generationCapabilities: async () => caps,
      previewPrompt: async (_sceneId, payload) => {
        previews.push(payload);
        return { execution_prompt: "A verified prompt", scene_revision: scene.revision, video_revision: video.revision, warnings: [], resolved_configuration: resolvedConfiguration(caps.qualified_capabilities.find(item => item.workflow_id === payload.workflow_id), payload) };
      },
    },
    "@/lib/api/scenes": { patchScene: async () => scene },
  }).GenerationEditor;

  function render() {
    cursor = 0;
    tree = Editor({ scene, scenes: [scene], video, onClose() {}, onSubmit: async payload => submitted.push(payload), isLoading: false });
    return tree;
  }
  const all = () => nodes(tree);
  const modeSelect = () => all().find(node => node.type === "select"
    && nodes(node).some(child => child.type === "option" && child.props.value === "AUTO"));
  const option = (select, value) => nodes(select).find(node => node.type === "option" && node.props.value === value);
  const button = label => {
    const found = all().find(node => node.props?.children === label && typeof node.props?.onClick === "function");
    assert.ok(found, `button ${label} exists`);
    return found;
  };
  function selectAsset(id, frameLabel) {
    const label = all().find(node => node.type === "label"
      && React.Children.toArray(node.props.children)[0] === frameLabel
      && nodes(node).some(child => child.type === "select"));
    const select = nodes(label).find(node => node.type === "select"
      && nodes(node).some(child => child.type === "option" && child.props.value === id));
    assert.ok(select, `asset selector contains ${id}`);
    select.props.onChange({ target: { value: id } });
    render();
  }
  render();
  return {
    all, button, modeSelect, option, previews, submitted, render, selectAsset,
    revoke() { caps = { ...caps, qualified_capabilities: [] }; },
  };
}

test("mode selection lists qualified alternatives beyond AUTO's currently derived mode", () => {
  const h = setup({ combinations: [
    capability("t2v"),
    capability("i2v", "i2v-high", { quality_profile: "HIGH" }),
    capability("fl2v"),
    capability("r2v"),
    capability("v2v", "aggregate-v2v", { execution_scope: "aggregate" }),
    capability("rv2v", "wide-rv2v", { aspect_ratio: "16:9" }),
  ] });
  const modes = h.modeSelect();
  assert.equal(h.option(modes, "i2v").props.disabled, true, "a different quality profile must not appear runnable");
  assert.equal(h.option(modes, "i2v_last").props.disabled, false, "qualified FL2V supports last-frame modes");
  assert.equal(h.option(modes, "i2v_first_last").props.disabled, false);
  assert.equal(h.option(modes, "r2v").props.disabled, false);
  assert.equal(h.option(modes, "v2v").props.disabled, true, "aggregate capability cannot enable a single-scene mode");
  assert.equal(h.option(modes, "rv2v").props.disabled, true, "other aspect ratios cannot enable a mode");

  const profile = h.all().find(node => node.type === "select"
    && nodes(node).some(child => child.type === "option" && child.props.value === "STANDARD")
    && !nodes(node).some(child => child.props.value === "AUTO"));
  assert.ok(profile);
  assert.equal(h.option(profile, "HIGH").props.disabled, false, "profiles qualified for another mode remain reachable");
  profile.props.onChange({ target: { value: "HIGH" } });
  h.render();
  assert.equal(h.option(h.modeSelect(), "i2v").props.disabled, false);
});

test("source support without a qualified workflow does not enable FL2V modes", () => {
  const h = setup({ combinations: [capability("t2v")], sourceTasks: ["fl2v"] });
  assert.equal(h.option(h.modeSelect(), "i2v_last").props.disabled, true);
  assert.equal(h.option(h.modeSelect(), "i2v_first_last").props.disabled, true);
});

for (const scenario of [
  { name: "T2V", selected: [], workflow: "t2v" },
  { name: "first frame", selected: [["first", "First frame"]], workflow: "i2v" },
  { name: "last frame", selected: [["last", "Last frame"]], workflow: "fl2v" },
  { name: "first and last frames", selected: [["first", "First frame"], ["last", "Last frame"]], workflow: "fl2v" },
]) {
  test(`AUTO resolves a qualified ${scenario.name} workflow`, async () => {
    const h = setup({
      combinations: [capability("t2v"), capability("i2v"), capability("fl2v")],
      assets: ["first", "last"].map(id => ({ id, filename: id, status: "READY", content_type: "image/png" })),
    });
    for (const [id, frameLabel] of scenario.selected) h.selectAsset(id, frameLabel);
    assert.equal(h.button("Preview prompt").props.disabled, false);
    await h.button("Preview prompt").props.onClick();
    h.render();
    assert.equal(h.previews[0].mode, "AUTO");
    assert.equal(h.previews[0].workflow_id, scenario.workflow);
    assert.equal(h.previews[0].first_frame_asset_id ?? null, scenario.selected.some(([id]) => id === "first") ? "first" : null);
    assert.equal(h.previews[0].last_frame_asset_id ?? null, scenario.selected.some(([id]) => id === "last") ? "last" : null);
  });
}

test("revoked capability while the editor is open blocks preview and generation", async () => {
  const h = setup({ combinations: [capability("t2v")] });
  await h.button("Preview prompt").props.onClick();
  h.render();
  await h.button("Accept exact prompt").props.onClick();
  h.render();
  assert.equal(h.button("Generate video with accepted prompt").props.disabled, false);

  h.revoke();
  h.render();
  assert.equal(h.button("Preview prompt").props.disabled, true);
  assert.equal(h.button("Generate video with accepted prompt").props.disabled, true);
  await h.button("Generate video with accepted prompt").props.onClick();
  assert.deepEqual(h.submitted, []);
});

test("changing a selected frame asset invalidates the accepted prompt", async () => {
  const h = setup({
    combinations: [capability("t2v"), capability("i2v")],
    assets: [{ id: "first", filename: "first", status: "READY", content_type: "image/png" }],
  });
  await h.button("Preview prompt").props.onClick();
  h.render();
  await h.button("Accept exact prompt").props.onClick();
  h.render();
  assert.equal(h.button("Generate video with accepted prompt").props.disabled, false);

  h.selectAsset("first", "First frame");
  assert.equal(h.button("Generate video with accepted prompt").props.disabled, true);
  await h.button("Generate video with accepted prompt").props.onClick();
  assert.deepEqual(h.submitted, []);
});
