const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

test("reuse preserves an explicit snapshot audio policy", () => {
  const { reuseGenerationSettings } = loadTypeScript("lib/generation/history-settings.ts");
  const policy = { mode: "source" };
  const input = reuseGenerationSettings({ mode: "v2v", input_snapshot: { audio_policy: policy } });
  assert.deepEqual(input.audio_policy, policy);
  assert.notEqual(input.audio_policy, policy);
});

test("reuse copies frozen profile, ratio and ordered roles into a new scene draft", () => {
  const { reuseGenerationSettings } = loadTypeScript("lib/generation/history-settings.ts");
  const generation = { mode: "r2v", input_snapshot: { mode: "r2v", requested_quality_profile: "HIGH", requested_aspect_ratio: "16:9", seed: 123, seed_policy: "FIXED", assets: [
    { id: "second", role: "REFERENCE_IMAGE", order_index: 1 }, { id: "first", role: "REFERENCE_IMAGE", order_index: 0 },
  ] } };
  const before = JSON.stringify(generation);
  assert.deepEqual(reuseGenerationSettings(generation), { mode: "r2v", quality_profile: "HIGH", aspect_ratio: "16:9", seed: 123, seed_policy: "FIXED", first_frame_asset_id: null, last_frame_asset_id: null, source_video_asset_id: null, reference_image_asset_ids: ["first", "second"], reference_video_asset_ids: [], reference_audio_asset_ids: [] });
  assert.equal(JSON.stringify(generation), before);
});

test("legacy settings reuse does not invent ratio or lose unsafe seed precision", () => {
  const { reuseGenerationSettings } = loadTypeScript("lib/generation/history-settings.ts");
  const input = reuseGenerationSettings({ mode: "i2v_last", input_snapshot: { seed: Number.MAX_SAFE_INTEGER + 1, assets: [{ id: "last", role: "LAST_FRAME" }] } });
  assert.equal(input.mode, "i2v_last");
  assert.equal(input.last_frame_asset_id, "last");
  assert.equal(input.aspect_ratio, undefined);
  assert.equal(input.seed_policy, "RANDOM");
  assert.equal(input.seed, null);
});

test("reuse restores Director source video and structured advanced settings", () => {
  const { reuseGenerationSettings } = loadTypeScript("lib/generation/history-settings.ts");
  const input = reuseGenerationSettings({ mode: "rv2v", input_snapshot: {
    mode: "rv2v", requested_aspect_ratio: "3:2", requested_width: 1536, requested_height: 1024,
    motion_context: { enabled: true, context_frames: 39 }, refine: { enabled: true },
    face_refine: { enabled: true }, audio: { enabled: true, preserve_native_audio: true },
    assets: [{ id: "source", role: "SOURCE_VIDEO" }, { id: "ref", role: "REFERENCE_VIDEO", order_index: 0 }],
  } });
  assert.equal(input.mode, "rv2v");
  assert.equal(input.source_video_asset_id, "source");
  assert.equal(input.aspect_ratio, "3:2");
  assert.equal(Object.hasOwn(input, "width"), false);
  assert.equal(Object.hasOwn(input, "height"), false);
  assert.equal(input.motion_context.context_frames, 39);
  assert.equal(input.audio_policy.mode, "generate");
});

test("Reuse never turns a random historical resolved seed into draft intent", () => {
  const { reuseGenerationSettings } = loadTypeScript("lib/generation/history-settings.ts");
  const input = reuseGenerationSettings({ mode: "t2v", input_snapshot: { seed: 123, seed_policy: "RANDOM" } });
  assert.equal(input.seed_policy, "RANDOM");
  assert.equal(input.seed, null);
});
