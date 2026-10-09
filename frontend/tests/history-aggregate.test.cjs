const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");

function nodes(node) { return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)]; }

for (const [aggregate, parentMotion] of [[false, false], [true, false], [false, true]]) test(`historical parent actions follow current chain and frozen Motion Context: ${aggregate}/${parentMotion}`, () => {
  const first = { id: "A", enabled: true, scene_order: 0, spec: { continuity: "CUT" }, generation_config: {} };
  const scenes = [first];
  if (aggregate) scenes.push({ id: "B", enabled: true, scene_order: 1, spec: { continuity: "CONTINUOUS" } });
  const calls = [];
  const History = loadTypeScript("components/generation/history-details.tsx", {
    "@tanstack/react-query": { useQuery: ({ enabled }) => enabled !== undefined
      ? { data: { url: "https://example.test/ready.mp4" } }
      : { data: { available: true, combinations: [{ workflow_id: "standalone", mode: "t2v", execution_scope: "single_scene", quality_profile: "STANDARD", aspect_ratio: "9:16" }] } } },
  }).GenerationHistoryDetails;
  const tree = History({
    generation: { id: "old", scene_id: "A", workflow_id: "standalone", mode: "t2v", operation: "ORIGINAL", status: "COMPLETED", output_asset_id: "ready", input_snapshot: { requested_quality_profile: "STANDARD", requested_aspect_ratio: "9:16", prompt: "Historical prompt", ...(parentMotion ? { director_execution: { canvas: { width: 864, height: 480 }, motion_context: { enabled: true }, audio_policy: {} } } : {}) } },
    scene: first, scenes, busy: false,
    onRegenerate: () => calls.push("regenerate"), onVariation: () => calls.push("variation"), onReuse: () => calls.push("reuse"),
  });
  const button = (label) => nodes(tree).find((node) => node.props?.children === label);
  for (const label of ["Tạo lại từ cấu hình đã chụp", "Tạo biến thể từ cấu hình đã chụp"]) {
    assert.equal(button(label).props.disabled, aggregate || parentMotion);
    button(label).props.onClick();
  }
  assert.equal(button("Dùng lại cấu hình cho cảnh").props.disabled, false);
  button("Dùng lại cấu hình cho cảnh").props.onClick();
  assert.deepEqual(calls, aggregate || parentMotion ? ["reuse"] : ["regenerate", "variation", "reuse"]);
  assert.ok(nodes(tree).some((node) => node.type === "a" && node.props.children === "Tải xuống"));
  assert.ok(nodes(tree).some((node) => node.type === "pre" && node.props.children === "Historical prompt"));
  if (aggregate) assert.ok(nodes(tree).some((node) => typeof node.props?.children === "string" && node.props.children.includes("Tạo toàn bộ")));
});

test("page history mutations reject old standalone parents after the current scene joins a chain", () => {
  for (const member of [0, 1, 2]) {
    const scenes = [0, 1, 2].map((index) => ({ id: `scene-${index}`, enabled: true, scene_order: index, spec: { continuity: index === 0 ? "CUT" : "CONTINUOUS" }, generation_config: {} }));
    const video = { id: "video", revision: 1, config: {}, scenes };
    const sent = [];
    const harness = pageHarness(video, { "@/lib/api/generations": {
      ...loadTypeScript("lib/api/generations.ts"),
      createVariation: async (...args) => sent.push(args), regenerate: async (...args) => sent.push(args),
    } });
    const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
    harness.render(page, { videoId: video.id });
    for (const name of ["createVariation", "regenerate"]) {
      const mutation = harness.mutations.find((item) => item.mutationFn.toString().includes(name));
      assert.ok(mutation);
      assert.throws(() => mutation.mutationFn({ id: "historical", scene_id: scenes[member].id, input_snapshot: {} }), /Tạo toàn bộ/);
    }
    assert.deepEqual(sent, []);
  }
});
