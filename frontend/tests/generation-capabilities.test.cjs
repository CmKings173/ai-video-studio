const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");
const api = () => loadTypeScript("lib/generation/capabilities.ts");
const combo = (mode = "r2v") => ({ mode, execution_scope: "single_scene", quality_profile: "STANDARD", aspect_ratio: "9:16", max_reference_images: 9, max_reference_videos: 3, max_reference_audio: 3, max_total_reference_files: 12, required_asset_slots: [], clip_min_seconds: 2, clip_max_seconds: 15, category_total_max_seconds: 15, reference_video_fps: 24, audio_requires_visual_reference: true });
const asset = (id, kind, duration = 5, fps = 24) => ({ id, status: "READY", content_type: `${kind}/test`, media_metadata: { [`${kind}_duration_seconds`]: duration, fps } });

test("Auto distinguishes last frame, both frames and references without assuming a workflow", () => {
  const { deriveMode } = api();
  assert.equal(deriveMode({}), "t2v");
  assert.equal(deriveMode({ last_frame_asset_id: "last" }), "i2v_last");
  assert.equal(deriveMode({ first_frame_asset_id: "first", last_frame_asset_id: "last" }), "i2v_first_last");
  assert.equal(deriveMode({ reference_audio_asset_ids: ["audio"] }), "r2v");
  assert.equal(deriveMode({ source_video_asset_id: "source" }), "v2v");
  assert.equal(deriveMode({ source_video_asset_id: "source", reference_image_asset_ids: ["image"] }), "rv2v");
});

test("capability lookup requires enabled mode, profile and ratio together", () => {
  const { matchingCapability } = api();
  const caps = { available: true, combinations: [combo("i2v_last")] };
  const input = { mode: "AUTO", last_frame_asset_id: "last", quality_profile: "STANDARD", aspect_ratio: "9:16" };
  assert.deepEqual(matchingCapability(caps, input), combo("i2v_last"));
  assert.equal(matchingCapability({ ...caps, available: false }, input), null);
  assert.equal(matchingCapability(caps, { ...input, quality_profile: "HIGH" }), null);
  assert.equal(matchingCapability(caps, { ...input, aspect_ratio: "16:9" }), null);
  const next = { available: true, qualified_capabilities: { combinations: [combo("v2v")] } };
  assert.deepEqual(matchingCapability(next, { mode: "v2v", source_video_asset_id: "source", quality_profile: "STANDARD", aspect_ratio: "9:16" }), combo("v2v"));
});

test("unqualified and mismatched frame combinations block submission", () => {
  const { generationInputProblems } = api();
  assert.ok(generationInputProblems({}, null, []).length);
  assert.ok(generationInputProblems({ mode: "i2v" }, combo("i2v"), []).length);
  assert.ok(generationInputProblems({ first_frame_asset_id: "same", last_frame_asset_id: "same" }, combo("i2v_first_last"), [asset("same", "image")]).length);
  assert.ok(generationInputProblems({ first_frame_asset_id: "first", reference_image_asset_ids: ["ref"] }, combo(), [asset("first", "image"), asset("ref", "image")]).length);
  assert.ok(generationInputProblems({ source_video_asset_id: "source", first_frame_asset_id: "first" }, combo("v2v"), [asset("source", "video"), asset("first", "image")]).length);
});

test("reference validation uses stream duration and refuses container fallback or invalid FPS", () => {
  const { generationInputProblems } = api();
  const input = { mode: "r2v", reference_video_asset_ids: ["video"] };
  assert.deepEqual(generationInputProblems(input, combo(), [asset("video", "video")]), []);
  assert.ok(generationInputProblems(input, combo(), [{ ...asset("video", "video"), duration_seconds: 5, media_metadata: {} }]).length);
  assert.ok(generationInputProblems(input, combo(), [asset("video", "video", 5, 30)]).length);
  assert.ok(generationInputProblems(input, combo(), [asset("video", "video", 1)]).length);
});

test("audio needs visual input and per-category totals cannot exceed 15 seconds", () => {
  const { generationInputProblems } = api();
  assert.ok(generationInputProblems({ reference_audio_asset_ids: ["audio"] }, combo(), [asset("audio", "audio")]).length);
  assert.ok(generationInputProblems({ reference_image_asset_ids: ["image"], reference_audio_asset_ids: ["a", "b"] }, combo(), [asset("image", "image"), asset("a", "audio", 10), asset("b", "audio", 10)]).length);
});

test("native slot variants must match requested ordered reference counts", () => {
  const { generationInputProblems } = api();
  const cap = { ...combo(), required_asset_slots: ["REFERENCE_IMAGE_1", "REFERENCE_IMAGE_2"] };
  assert.ok(generationInputProblems({ reference_image_asset_ids: ["a"] }, cap, [asset("a", "image")]).length);
  assert.deepEqual(generationInputProblems({ reference_image_asset_ids: ["a", "b"] }, cap, [asset("a", "image"), asset("b", "image")]), []);
  assert.ok(generationInputProblems({ reference_image_asset_ids: ["a", "a"] }, cap, [asset("a", "image")]).length);
});

test("fixed seed cannot silently lose integer precision", () => {
  const { generationInputProblems } = api();
  assert.ok(generationInputProblems({ seed_policy: "FIXED", seed: Number.MAX_SAFE_INTEGER + 1 }, combo("t2v"), []).length);
  assert.deepEqual(generationInputProblems({ seed_policy: "FIXED", seed: 0 }, combo("t2v"), []), []);
});
