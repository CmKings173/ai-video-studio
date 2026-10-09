const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");

function setup() {
  const video = { id: "video-1", revision: 4, config: {}, scenes: [
    { id: "scene-1", scene_order: 1, enabled: true, selected_generation_id: null, revision: 2, prompt: "bottle", negative_prompt: "", duration_seconds: 5, spec: {} },
  ] };
  const sent = [];
  const actual = loadTypeScript("lib/api/generations.ts");
  const harness = pageHarness(video, {
    "@/lib/api/generations": { ...actual, generateAll: async (...args) => { sent.push(args); throw new TypeError("lost response"); } },
  });
  const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
  const submit = async () => {
    harness.render(page, { videoId: video.id });
    await assert.rejects(harness.mutations.at(-1).mutationFn(), /lost response/);
    return sent.at(-1);
  };
  return { video, submit };
}

test("lost batch response retries the same semantic input and key with explicit revisions", async () => {
  const { video, submit } = setup();
  const first = await submit();
  const second = await submit();
  assert.deepEqual(second, first);
  assert.equal(first[1].expected_video_revision, video.revision);
  assert.deepEqual(first[1].expected_scene_revisions, { "scene-1": 2 });
  assert.deepEqual(first[1].scene_ids, ["scene-1"]);
});

test("execution progress and object property order retain an ambiguous retry key", async () => {
  const { video, submit } = setup();
  video.config = { seed: 1, workflow: "test" };
  const first = await submit();
  video.config = { workflow: "test", seed: 1 };
  video.scenes[0].last_generation_no = 7;
  video.scenes[0].active_generation_id = "running";
  const second = await submit();
  assert.deepEqual(second, first);
});

test("partial then full automatic completion preserves the lost batch payload and key", async () => {
  const { video, submit } = setup();
  video.scenes.push({ ...video.scenes[0], id: "scene-2", scene_order: 2 });
  const first = await submit();
  video.scenes[0].selected_generation_id = "output-1";
  video.scenes[0].revision++;
  video.revision++;
  assert.deepEqual(await submit(), first, "unfinished scene must not start another generation");
  video.scenes[1].selected_generation_id = "output-2";
  video.scenes[1].revision++;
  video.revision++;
  assert.deepEqual(await submit(), first, "the complete batch must still replay");
});

test("editor change after automatic completion rotates the pending action", async () => {
  const { video, submit } = setup();
  const first = await submit();
  video.scenes[0].selected_generation_id = "output-1";
  video.scenes[0].revision++;
  video.revision++;
  assert.deepEqual(await submit(), first);
  video.scenes[0].prompt = "An editor changed the prompt";
  video.scenes[0].revision++;
  video.revision++;
  assert.notEqual((await submit())[2], first[2]);
});

for (const [label, change] of [
  ["scene edit", (v) => { v.scenes[0].revision++; v.scenes[0].prompt = "new"; }],
  ["future generation config", (v) => { v.scenes[0].generation_config = { mode: "i2v", seed: 8 }; }],
  ["Director source video", (v) => { v.scenes[0].generation_config = { mode: "v2v", source_video_asset_id: "source-video" }; }],
  ["Director motion/refine/audio settings", (v) => { v.scenes[0].generation_config = { mode: "t2v", motion_context: { enabled: true, context_frames: 39 }, refine: { enabled: true }, face_refine: { enabled: true }, audio: { enabled: true, preserve_native_audio: true } }; }],
  ["added scene", (v) => { v.scenes.push({ ...v.scenes[0], id: "scene-2", scene_order: 2 }); v.revision++; }],
  ["disabled scene", (v) => { v.scenes[0].enabled = false; v.revision++; }],
  ["video config", (v) => { v.config = { prompt_enhancer: "new" }; }],
]) {
  test(`${label} rotates an ambiguous batch retry key`, async () => {
    const { video, submit } = setup();
    const first = await submit();
    change(video);
    const second = await submit();
    assert.notEqual(second[2], first[2]);
  });
}
