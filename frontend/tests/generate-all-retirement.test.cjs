const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");
const errors = loadTypeScript("lib/api/errors.ts");

function conflict(code, status = 409) {
  return new errors.ApiClientError(status, { error: { code, message: code } });
}

function setup() {
  const rendered = { id: "video-1", revision: 4, config: {}, scenes: [
    { id: "scene-1", scene_order: 1, enabled: true, selected_generation_id: null, revision: 2, prompt: "bottle", negative_prompt: "", duration_seconds: 5, spec: {} },
    { id: "scene-2", scene_order: 2, enabled: true, selected_generation_id: null, revision: 2, prompt: "bottle", negative_prompt: "", duration_seconds: 5, spec: {} },
  ] };
  const state = { fresh: structuredClone(rendered), failure: new TypeError("lost response"), refreshError: null, refreshes: 0 };
  const sent = [];
  const selections = [];
  const actual = loadTypeScript("lib/api/generations.ts");
  const harness = pageHarness(rendered, {
    "@/lib/api/errors": errors,
    "@/lib/api/generations": { ...actual, generateAll: async (...args) => { sent.push(args); throw state.failure; } },
    "@/lib/api/scenes": { selectGeneration: async (...args) => { selections.push(args); } },
  });
  let queryCursor = 0;
  harness.mocks["@tanstack/react-query"].useQuery = () => ({
    data: queryCursor++ === 0 ? rendered : undefined,
    isLoading: true,
    refetch: async () => {
      state.refreshes++;
      return { data: state.fresh, error: state.refreshError };
    },
  });
  const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
  function render() { queryCursor = 0; harness.render(page, { videoId: rendered.id }); }
  async function submit() {
    render();
    const action = harness.mutations.at(-1);
    try { await action.mutationFn(); assert.fail("request should fail"); }
    catch (error) { action.onError(error); return error; }
  }
  async function select(generationId) {
    render();
    const action = harness.mutations[2];
    await action.mutationFn({ sceneId: "scene-1", generationId, revision: state.fresh.scenes[0].revision });
    state.fresh.scenes[0].selected_generation_id = generationId;
    state.fresh.scenes[0].selected_generation_fresh = generationId !== null;
    state.fresh.scenes[0].revision++;
    state.fresh.revision++;
    action.onSuccess();
  }
  return { rendered, state, sent, selections, submit, select };
}

test("acknowledged manual deselect/reselect retires the batch and uses fresh detail despite stale render", async () => {
  const s = setup();
  await s.submit();
  const first = s.sent.at(-1);
  await s.select("variation-output");
  await s.submit();
  const selected = s.sent.at(-1);
  assert.notEqual(selected[2], first[2]);
  assert.deepEqual(selected[1].scene_ids, ["scene-2"]);
  assert.equal(selected[1].expected_video_revision, 5);
  await s.select(null);
  await s.submit();
  const deselected = s.sent.at(-1);
  assert.notEqual(deselected[2], selected[2]);
  assert.deepEqual(deselected[1].scene_ids, ["scene-1", "scene-2"]);
  assert.equal(deselected[1].expected_scene_revisions["scene-1"], 4);
  await s.select("variation-output");
  await s.submit();
  assert.notEqual(s.sent.at(-1)[2], deselected[2]);
  assert.deepEqual(s.sent.at(-1)[1].scene_ids, ["scene-2"]);
  assert.equal(s.state.refreshes, 3);
  assert.equal(s.selections.length, 3);
});

for (const [label, code, status, change] of [
  ["external revision-only save", "IDEMPOTENCY_KEY_REUSED", 409, (v) => { v.revision++; v.scenes[0].revision++; }],
  ["edit restored to original fields", "REVISION_CONFLICT", 409, (v) => { v.revision += 2; v.scenes[0].revision += 2; }],
  ["external manual selection", "IDEMPOTENCY_KEY_REUSED", 409, (v) => { v.revision++; v.scenes[0].revision++; v.scenes[0].selected_generation_id = "variation-output"; }],
  ["precondition revision conflict", "REVISION_CONFLICT", 412, (v) => { v.revision++; v.scenes[0].revision++; }],
]) {
  test(`${label}: definitive rejection refreshes detail and rotates payload/key`, async () => {
    const s = setup();
    await s.submit();
    const first = s.sent.at(-1);
    change(s.state.fresh);
    s.state.failure = conflict(code, status);
    await s.submit();
    assert.deepEqual(s.sent.at(-1), first, "the rejected replay still uses the original request");
    s.state.failure = new TypeError("lost response");
    await s.submit();
    const next = s.sent.at(-1);
    assert.equal(s.state.refreshes, 1);
    assert.notEqual(next[2], first[2]);
    assert.equal(next[1].expected_video_revision, s.state.fresh.revision);
    assert.notDeepEqual(next[1], first[1]);
    await s.submit();
    assert.deepEqual(s.sent.at(-1), next, "a new ambiguous request must also remain pinned");
    assert.equal(s.state.refreshes, 1);
  });
}

test("failed detail refresh sends no new batch and keeps the refresh requirement", async () => {
  const s = setup();
  await s.submit();
  s.state.failure = conflict("REVISION_CONFLICT");
  await s.submit();
  s.state.refreshError = new TypeError("detail unavailable");
  assert.equal((await s.submit()).message, "detail unavailable");
  assert.equal(s.sent.length, 2);
  assert.equal((await s.submit()).message, "detail unavailable");
  assert.equal(s.sent.length, 2);
  s.state.refreshError = null;
  s.state.fresh.revision++;
  s.state.failure = new TypeError("lost response");
  await s.submit();
  assert.equal(s.state.refreshes, 3);
  assert.equal(s.sent.at(-1)[1].expected_video_revision, 5);
});

for (const [label, failure] of [
  ["transport failure", new TypeError("lost response")],
  ["server failure", conflict("API_ERROR", 503)],
  ["server failure carrying a conflict code", conflict("REVISION_CONFLICT", 500)],
]) {
  test(`${label} preserves original replay across partial and full automatic completion`, async () => {
    const s = setup();
    s.state.failure = failure;
    await s.submit();
    const first = s.sent.at(-1);
    for (const scene of s.rendered.scenes) {
      scene.selected_generation_id = `original-${scene.id}`;
      scene.revision++;
      s.rendered.revision++;
      await s.submit();
      assert.deepEqual(s.sent.at(-1), first);
    }
    assert.equal(s.state.refreshes, 0);
  });
}
