const assert = require("node:assert/strict");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

// A semantic TypeScript program, not transpileModule or source-string matching.
function compileDetector(detector) {
  const filename = path.resolve(__dirname, "detector-contract-fixture.ts");
  const source = `import type { FaceRefineSettings } from "../lib/api/types";
const settings: FaceRefineSettings = { enabled: true, detector: ${JSON.stringify(detector)} };
void settings;`;
  const options = { noEmit: true, strict: true, skipLibCheck: true, types: [], target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS };
  const host = ts.createCompilerHost(options);
  const getSourceFile = host.getSourceFile.bind(host);
  host.getSourceFile = (file, languageVersion, ...rest) => path.resolve(file) === filename
    ? ts.createSourceFile(file, source, languageVersion, true)
    : getSourceFile(file, languageVersion, ...rest);
  return ts.getPreEmitDiagnostics(ts.createProgram([filename], options, host));
}

test("face detector contract compiles supported literal and rejects unsupported literal", () => {
  const supported = compileDetector("face_yolov8m.pt");
  assert.deepEqual(supported.map((d) => ts.flattenDiagnosticMessageText(d.messageText, "\n")), []);
  const unsupported = compileDetector("face_yolov8n.pt");
  assert.equal(unsupported.length, 1);
  assert.equal(unsupported[0].code, 2322);
  assert.match(ts.flattenDiagnosticMessageText(unsupported[0].messageText, "\n"), /face_yolov8m\.pt/);
});
