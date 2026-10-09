const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

test("batch preparation and summary include stale selected output and exclude fresh output", () => {
  const video = { id: "video", revision: 2, config: {}, scenes: [
    { id: "fresh", scene_order: 0, enabled: true, revision: 1, selected_generation_id: "a", selected_generation_fresh: true },
    { id: "stale", scene_order: 1, enabled: true, revision: 2, selected_generation_id: "b", selected_generation_fresh: false },
    { id: "legacy", scene_order: 2, enabled: true, revision: 1, selected_generation_id: "c" },
    { id: "empty", scene_order: 3, enabled: true, revision: 1, selected_generation_id: null },
    { id: "disabled", scene_order: 4, enabled: false, revision: 1, selected_generation_fresh: false },
  ] };
  const { prepareGenerateAll } = loadTypeScript("lib/api/generations.ts");
  const { batchSummary } = loadTypeScript("lib/generation/batch-summary.ts");
  assert.deepEqual(prepareGenerateAll(video).payload.scene_ids, ["stale", "legacy", "empty"]);
  assert.deepEqual(batchSummary(video).map((scene) => scene.id), ["stale", "legacy", "empty"]);
});

test("stale continuous member includes its fresh native chain in IDs, revisions and summary", () => {
  const video = { id: "video", revision: 4, config: {}, scenes: [
    { id: "first", scene_order: 0, enabled: true, revision: 2, selected_generation_fresh: true, spec: { continuity: "CUT" } },
    { id: "edited", scene_order: 1, enabled: true, revision: 3, selected_generation_fresh: false, spec: { continuity: "CONTINUOUS" } },
    { id: "last", scene_order: 2, enabled: true, revision: 1, selected_generation_fresh: true, spec: { continuity: "CONTINUOUS" } },
    { id: "separate", scene_order: 3, enabled: true, revision: 1, selected_generation_fresh: true, spec: { continuity: "CUT" } },
  ] };
  const { prepareGenerateAll } = loadTypeScript("lib/api/generations.ts");
  const { batchSummary } = loadTypeScript("lib/generation/batch-summary.ts");
  const prepared = prepareGenerateAll(video);
  assert.deepEqual(prepared.payload.scene_ids, ["first", "edited", "last"]);
  assert.deepEqual(prepared.payload.expected_scene_revisions, { first: 2, edited: 3, last: 1 });
  assert.deepEqual(batchSummary(video).map((scene) => scene.id), prepared.payload.scene_ids);
});
