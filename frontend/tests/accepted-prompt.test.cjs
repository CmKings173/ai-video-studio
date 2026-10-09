const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

function setup(settings = { mode: "r2v", reference_image_asset_ids: ["a", "b"] }) {
  const api = loadTypeScript("lib/generation/accepted-prompt.ts");
  const context = api.createPromptContext("scene-1", 2, "video-1", 4, settings);
  const preview = { execution_prompt: "  Bottle\n  @Image1 @Image2  ", raw_prompt: "Bottle", enhanced: true, warnings: [], scene_revision: 2, video_revision: 4 };
  return { api, context, preview, settings };
}

test("acceptance preserves edited prompt byte-for-byte and freezes settings", () => {
  const { api, context, preview, settings } = setup();
  const edited = "  Edited\n\t@Image1 café  ";
  const accepted = api.acceptPrompt(context, preview, edited);
  settings.reference_image_asset_ids.reverse();
  const payload = api.acceptedGenerationRequest(accepted, context);
  assert.equal(payload.execution_prompt, edited);
  assert.equal(payload.source_scene_revision, 2);
  assert.equal(payload.source_video_revision, 4);
  assert.deepEqual(payload.reference_image_asset_ids, ["a", "b"]);
  payload.reference_image_asset_ids.reverse();
  assert.deepEqual(api.acceptedGenerationRequest(accepted, context).reference_image_asset_ids, ["a", "b"]);
});

for (const [label, args] of [
  ["another scene", ["scene-2", 2, "video-1", 4]],
  ["another video", ["scene-1", 2, "video-2", 4]],
  ["scene revision", ["scene-1", 3, "video-1", 4]],
  ["video revision", ["scene-1", 2, "video-1", 5]],
]) {
  test(`${label} invalidates accepted text before submission`, () => {
    const { api, context, preview, settings } = setup();
    const accepted = api.acceptPrompt(context, preview, preview.execution_prompt);
    const current = api.createPromptContext(...args, settings);
    assert.equal(api.isAcceptedPromptCurrent(accepted, current), false);
    assert.throws(() => api.acceptedGenerationRequest(accepted, current), /preview/i);
  });
}

test("reference order and configuration changes invalidate acceptance", () => {
  const { api, context, preview } = setup();
  const accepted = api.acceptPrompt(context, preview, preview.execution_prompt);
  for (const settings of [
    { mode: "r2v", reference_image_asset_ids: ["b", "a"] },
    { mode: "r2v", reference_image_asset_ids: ["a", "b"], quality_profile: "HIGH" },
    { mode: "rv2v", source_video_asset_id: "video", reference_image_asset_ids: ["a", "b"], motion_context: { enabled: true, context_frames: 39 } },
  ]) {
    const current = api.createPromptContext("scene-1", 2, "video-1", 4, settings);
    assert.throws(() => api.acceptedGenerationRequest(accepted, current), /preview/i);
  }
});

test("object field order and omitted undefined settings do not invalidate acceptance", () => {
  const { api, context, preview } = setup();
  const accepted = api.acceptPrompt(context, preview, preview.execution_prompt);
  const current = api.createPromptContext("scene-1", 2, "video-1", 4, { reference_image_asset_ids: ["a", "b"], mode: "r2v", seed: undefined });
  assert.equal(api.isAcceptedPromptCurrent(accepted, current), true);
});

test("late preview from an earlier revision cannot be accepted", () => {
  const { api, context, preview } = setup();
  assert.throws(() => api.acceptPrompt(context, { ...preview, scene_revision: 1 }, "text"), /preview/i);
  assert.throws(() => api.acceptPrompt(context, { ...preview, video_revision: 3 }, "text"), /preview/i);
});

test("empty and oversized accepted text are rejected while whitespace within text is preserved", () => {
  const { api, context, preview } = setup();
  assert.throws(() => api.acceptPrompt(context, preview, " \n\t"), /prompt/i);
  assert.throws(() => api.acceptPrompt(context, preview, "x".repeat(30001)), /prompt/i);
});
