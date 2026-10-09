const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");

const scene = (id, order, continuity = "CUT", enabled = true) => ({
  id, scene_order: order, enabled, revision: 1, prompt: id, spec: { continuity },
  duration_seconds: 5, generation_config: {}, selected_generation_fresh: true,
});

test("canonical chains sort enabled scenes using actual adjacency and include the first CUT", () => {
  const { continuityChains, sceneRequiresAggregate, eligibleScenes } = loadTypeScript("lib/generation/eligible-scenes.ts");
  const rows = [scene("C", 2, "CONTINUOUS"), scene("D", 3), scene("A", 0), scene("B", 1, "CONTINUOUS")];
  assert.deepEqual(continuityChains(rows).map((chain) => chain.map((s) => s.id)), [["A", "B", "C"], ["D"]]);
  for (const id of ["A", "B", "C"]) assert.equal(sceneRequiresAggregate(rows.find((s) => s.id === id), rows), true);
  assert.equal(sceneRequiresAggregate(rows[1], rows), false);
  rows[0].selected_generation_fresh = false;
  assert.deepEqual(eligibleScenes(rows).map((s) => s.id), ["A", "B", "C"]);
  rows.find((s) => s.id === "B").enabled = false;
  assert.deepEqual(continuityChains(rows).map((chain) => chain.map((s) => s.id)), [["A"], ["C"], ["D"]]);
  assert.equal(sceneRequiresAggregate(rows.find((s) => s.id === "A"), rows), false);
  assert.deepEqual(continuityChains([scene("gap", 5, "CONTINUOUS"), scene("first", 0)]).map((c) => c.length), [1, 1]);
});

for (const member of ["A", "B", "C", "D"]) test(`page direct submission guard for ${member}`, async () => {
  const rows = [scene("A", 0), scene("B", 1, "CONTINUOUS"), scene("C", 2, "CONTINUOUS"), scene("D", 3)];
  const video = { id: "video", revision: 1, config: {}, scenes: rows };
  const sent = [];
  const harness = pageHarness(video, { "@/lib/api/generations": {
    ...loadTypeScript("lib/api/generations.ts"), createGeneration: async (...args) => { sent.push(args); },
  } });
  const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
  harness.render(page, { videoId: video.id });
  const mutation = harness.mutations.find((m) => m.mutationFn.toString().includes("createGeneration"));
  if (member === "D") {
    await mutation.mutationFn({ sceneId: member, payload: {} });
    assert.equal(sent.length, 1);
  } else {
    assert.throws(() => mutation.mutationFn({ sceneId: member, payload: {} }), /Tạo toàn bộ/);
    assert.deepEqual(sent, []);
  }
});

test("Generate Clip is disabled for chain members including CUT, independent CUT stays available", () => {
  for (const continuous of [true, false]) {
    const video = { id: "video", revision: 1, config: {}, scenes: [scene("A", 0), scene("B", 1, continuous ? "CONTINUOUS" : "CUT")] };
    let queryIndex = 0;
    const harness = pageHarness(video);
    harness.mocks.react.useEffect = () => {};
    harness.mocks["@tanstack/react-query"].useQuery = () => ({ data: queryIndex++ === 0 ? video : undefined, isLoading: false });
    const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
    const tree = page({ params: { videoId: video.id } });
    function nodes(node) { return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
    const button = nodes(tree).find((node) => React.Children.toArray(node.props?.children).some((child) => child.props?.children === "Cấu hình & tạo clip"));
    assert.ok(button);
    assert.equal(button.props.disabled, continuous);
  }
});

test("capability scopes never substitute aggregate or unknown scope for direct generation", () => {
  const { singleSceneCapability, aggregateCapability } = loadTypeScript("lib/generation/capabilities.ts");
  const base = { mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16" };
  const aggregate = { ...base, execution_scope: "aggregate", workflow_id: "aggregate" };
  const single = { ...base, execution_scope: "single_scene", workflow_id: "single" };
  const caps = { available: true, combinations: [aggregate, base, single] };
  assert.equal(singleSceneCapability(caps, base).workflow_id, "single");
  assert.equal(aggregateCapability(caps, base).workflow_id, "aggregate");
  assert.equal(singleSceneCapability({ ...caps, combinations: [aggregate, base] }, base), null);
  assert.equal(aggregateCapability({ ...caps, available: false }, base), null);
  assert.equal(aggregateCapability(caps, { ...base, quality_profile: "HIGH" }), null);
});

test("Motion Context requires aggregate even on an independent CUT and inherited saved settings", () => {
  const { directGenerationRequiresAggregate } = loadTypeScript("lib/generation/eligible-scenes.ts");
  const row = scene("A", 0);
  row.generation_config = { motion_context: { enabled: true } };
  assert.equal(directGenerationRequiresAggregate(row, [row]), true);
  assert.equal(directGenerationRequiresAggregate(row, [row], {}), true);
  assert.equal(directGenerationRequiresAggregate(row, [row], { motion_context: { enabled: false } }), false);
});

test("page never calls createGeneration for saved or requested Motion Context", () => {
  for (const saved of [true, false]) {
    const row = scene("A", 0);
    if (saved) row.generation_config = { motion_context: { enabled: true } };
    const video = { id: "video", revision: 1, config: {}, scenes: [row] };
    const sent = [];
    const harness = pageHarness(video, { "@/lib/api/generations": {
      ...loadTypeScript("lib/api/generations.ts"), createGeneration: async (...args) => sent.push(args),
    } });
    const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
    harness.render(page, { videoId: video.id });
    const mutation = harness.mutations.find((m) => m.mutationFn.toString().includes("createGeneration"));
    assert.throws(() => mutation.mutationFn({ sceneId: row.id, payload: saved ? {} : { motion_context: { enabled: true } } }), /Tạo toàn bộ/);
    assert.deepEqual(sent, []);
  }
});

test("both CUT heads in separate native chains require aggregate; gaps never bridge", () => {
  const { continuityChains, sceneRequiresAggregate } = loadTypeScript("lib/generation/eligible-scenes.ts");
  const rows = [scene("A", 0), scene("B", 1, "CONTINUOUS"), scene("C", 2), scene("D", 3, "CONTINUOUS"), scene("E", 5)];
  assert.deepEqual(continuityChains(rows).map((chain) => chain.map((s) => s.id)), [["A", "B"], ["C", "D"], ["E"]]);
  assert.deepEqual(rows.map((row) => sceneRequiresAggregate(row, rows)), [true, true, true, true, false]);
});

for (const reason of ["persisted Motion Context", "first CUT of native chain"]) {
  test(`aggregate settings can reopen for ${reason} while direct submission stays blocked`, async () => {
    const first = scene("A", 0);
    const rows = [first];
    if (reason === "persisted Motion Context") first.generation_config = { motion_context: { enabled: true } };
    else rows.push(scene("B", 1, "CONTINUOUS"));
    const video = { id: "video", revision: 1, config: {}, scenes: rows };
    const states = [];
    const sent = [];
    let stateIndex = 0;
    let queryIndex = 0;
    function EditorMarker() { return null; }
    const harness = pageHarness(video, {
      "@/components/generation/generation-editor": { GenerationEditor: EditorMarker },
      "@/lib/api/generations": {
        ...loadTypeScript("lib/api/generations.ts"), createGeneration: async (...args) => sent.push(args),
      },
    });
    harness.mocks.react.useEffect = () => {};
    harness.mocks.react.useState = (initial) => {
      const index = stateIndex++;
      if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], (next) => { states[index] = typeof next === "function" ? next(states[index]) : next; }];
    };
    harness.mocks["@tanstack/react-query"].useQuery = () => ({ data: queryIndex++ === 0 ? video : undefined, isLoading: false });
    const recordMutation = harness.mocks["@tanstack/react-query"].useMutation;
    harness.mocks["@tanstack/react-query"].useMutation = (options) => {
      recordMutation(options);
      return { mutateAsync: async (variables) => options.mutationFn(variables) };
    };
    const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
    let tree;
    function render() {
      stateIndex = 0; queryIndex = 0;
      harness.render((props) => { tree = page(props); }, { videoId: video.id });
    }
    function nodes(node) { return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }
    function directButton() {
      return nodes(tree).find((node) => React.Children.toArray(node.props?.children).some((child) => child.props?.children === "Cấu hình & tạo clip"));
    }
    function settingsButton() { return nodes(tree).find((node) => node.props?.children === "Cấu hình tạo clip"); }
    render();
    assert.equal(directButton().props.disabled, true);
    assert.notEqual(settingsButton().props.disabled, true);
    settingsButton().props.onClick(); render();
    let editor = nodes(tree).find((node) => node.type === EditorMarker);
    assert.ok(editor, "settings button must open GenerationEditor");
    assert.equal(editor.props.scene.id, first.id);
    assert.deepEqual(editor.props.scenes, rows);
    editor.props.onClose(); render();
    assert.equal(nodes(tree).some((node) => node.type === EditorMarker), false);
    assert.equal(directButton().props.disabled, true);
    assert.notEqual(settingsButton().props.disabled, true);
    settingsButton().props.onClick(); render();
    editor = nodes(tree).find((node) => node.type === EditorMarker);
    assert.ok(editor, "settings button must reopen GenerationEditor after closing");
    await assert.rejects(editor.props.onSubmit({}), /Tạo toàn bộ/);
    assert.deepEqual(sent, []);
    assert.equal(directButton().props.disabled, true);
  });
}
