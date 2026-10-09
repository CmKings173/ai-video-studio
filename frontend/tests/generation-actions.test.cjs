const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript, pageHarness } = require("./load-typescript.cjs");

function setup() {
  const video = { id: "video", revision: 2, config: {}, scenes: [{ id: "scene", scene_order: 0, revision: 3, enabled: true, selected_generation_id: null, prompt: "scene prompt", negative_prompt: "", duration_seconds: 5, spec: {} }] };
  const sent = [];
  const actual = loadTypeScript("lib/api/generations.ts");
  const harness = pageHarness(video, { "@/lib/api/generations": { ...actual,
    createGeneration: async (...args) => { sent.push(["generate", ...args]); throw new TypeError("lost response"); },
    createVariation: async (...args) => { sent.push(["variation", ...args]); throw new TypeError("lost response"); },
    regenerate: async (...args) => { sent.push(["regenerate", ...args]); throw new TypeError("lost response"); },
  } });
  const page = loadTypeScript("app/videos/[videoId]/page.tsx", harness.mocks).default;
  const submit = async (name, variables) => {
    harness.render(page, { videoId: video.id });
    const mutation = harness.mutations.find((item) => item.mutationFn.toString().includes(name));
    assert.ok(mutation, `${name} must be connected to the actual page`);
    await assert.rejects(mutation.mutationFn(variables), /lost response/);
    return sent.at(-1);
  };
  return { submit };
}

test("lost creation response retains exact accepted text and key, but another scene gets a new key", async () => {
  const { submit } = setup();
  const input = { sceneId: "scene", payload: { execution_prompt: "  text\n  ", source_scene_revision: 3, source_video_revision: 2 } };
  const first = await submit("createGeneration", input);
  assert.deepEqual(await submit("createGeneration", input), first);
  assert.equal(first[2].execution_prompt, input.payload.execution_prompt);
  const secondScene = await submit("createGeneration", { ...input, sceneId: "another-scene" });
  assert.notEqual(secondScene[3], first[3]);
});

test("Variation sends only parent ID to its own scene and preserves ambiguous retry", async () => {
  const { submit } = setup();
  const parent = { id: "parent", scene_id: "parent-scene", workflow_id: "old", input_snapshot: { prompt: "frozen", seed: 9, width: 480, height: 864 } };
  const first = await submit("createVariation", parent);
  assert.equal(first[1], "parent-scene");
  assert.deepEqual(first[2], { parent_generation_id: "parent" });
  assert.deepEqual(await submit("createVariation", parent), first);
  assert.notEqual((await submit("createVariation", { ...parent, id: "other-parent" }))[3], first[3]);
});

test("Regenerate targets the parent scene and keeps the lost-response key", async () => {
  const { submit } = setup();
  const parent = { id: "parent", scene_id: "parent-scene" };
  const first = await submit("regenerate", parent);
  assert.equal(first[1], "parent-scene");
  assert.equal(first[2], "parent");
  assert.deepEqual(await submit("regenerate", parent), first);
});
