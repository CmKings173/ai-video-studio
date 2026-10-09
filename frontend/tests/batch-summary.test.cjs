const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

test("batch summary matches ordered eligible scenes and each scene's stored config", () => {
  const { batchSummary } = loadTypeScript("lib/generation/batch-summary.ts");
  const video = { aspect_ratio: "9:16", scenes: [
    { id: "later", scene_order: 2, enabled: true, selected_generation_id: null, duration_seconds: 10, generation_config: { mode: "AUTO", last_frame_asset_id: "last", quality_profile: "HIGH", aspect_ratio: "16:9", seed_policy: "FIXED", seed: 42 } },
    { id: "first", scene_order: 0, enabled: true, selected_generation_id: null, duration_seconds: 5 },
    { id: "selected", scene_order: 1, enabled: true, selected_generation_id: "done", selected_generation_fresh: true, duration_seconds: 5 },
    { id: "disabled", scene_order: 3, enabled: false, selected_generation_id: null, duration_seconds: 5 },
  ] };
  const result = batchSummary(video);
  assert.deepEqual(result.map((row) => row.id), ["first", "later"]);
  assert.equal(result[0].mode, "t2v"); assert.equal(result[0].quality, "STANDARD"); assert.equal(result[0].ratio, "9:16");
  assert.equal(result[1].mode, "i2v_last"); assert.equal(result[1].quality, "HIGH"); assert.equal(result[1].ratio, "16:9"); assert.equal(result[1].seed, 42);
});
