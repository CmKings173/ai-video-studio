const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");
const { assemblyFormSchema } = loadTypeScript("lib/assembly/settings.ts");
const defaults = { fit_mode: "FIT_PAD", transition: "CUT", crossfade_seconds: 0.5,
  audio_mode: "KEEP_SCENE_AUDIO", background_volume: 0.3, fps: 24 };

test("delivery preset ignores stale invalid custom dimensions", () => {
  assert.equal(assemblyFormSchema.safeParse({ ...defaults, delivery_preset: "ULTRAWIDE_2560_1080", width: "", height: 7 }).success, true);
});

test("custom delivery validates even dimensions and range", () => {
  assert.equal(assemblyFormSchema.safeParse({ ...defaults, delivery_preset: "", width: 1080, height: 1920 }).success, true);
  for (const width of [0, 255, 257, 4098]) {
    assert.equal(assemblyFormSchema.safeParse({ ...defaults, delivery_preset: "", width, height: 1920 }).success, false);
  }
});

test("preset does not bypass frame rate or fit mode validation", () => {
  const preset = { ...defaults, delivery_preset: "ULTRAWIDE_2560_1080" };
  assert.equal(assemblyFormSchema.safeParse({ ...preset, fps: 60 }).success, false);
  assert.equal(assemblyFormSchema.safeParse({ ...preset, fit_mode: "STRETCH" }).success, false);
});
