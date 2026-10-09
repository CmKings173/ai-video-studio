const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");
const { isCurrentFinal } = loadTypeScript("lib/utils/current-final.ts");

test("historical promoted pointer is current only while video is READY", () => {
  for (const status of ["DIRTY", "ASSEMBLING", "GENERATING", "SCENES_READY", "ARCHIVED"]) {
    assert.equal(isCurrentFinal({ status, current_final_video_id: "final" }, "final"), false);
  }
  assert.equal(isCurrentFinal({ status: "READY", current_final_video_id: "final" }, "final"), true);
  assert.equal(isCurrentFinal({ status: "READY", current_final_video_id: "old" }, "final"), false);
  assert.equal(isCurrentFinal(undefined, "final"), false);
});
