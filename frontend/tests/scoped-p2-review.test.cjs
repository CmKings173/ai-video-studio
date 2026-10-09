const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const { loadTypeScript } = require("./load-typescript.cjs");
const asset = (status = "VALIDATING") => ({ id: "asset", status });
function apiHarness() {
  return { api: loadTypeScript("lib/api/assets.ts", { "./client": { apiClient: {} } }) };
}
function nodes(node) {
  if (!node || typeof node !== "object") return [];
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function uploadHarness(options = {}) {
  options = { policy: { max_upload_bytes: 100, allowed_content_types: ["image/png"] }, ...options };
  const states = [], refs = [], effects = [];
  let cursor = 0, refCursor = 0, effectCursor = 0, queryCursor = 0, infiniteCursor = 0, tree;
  const calls = [], invalidations = [], timers = new Map(), delays = []; let timerId = 0;
  const action = { getKey: () => "key", reset: () => calls.push(["reset"]) };
  const client = { invalidateQueries: async (value) => invalidations.push(value) };
  const api = apiHarness().api;
  let observed;
  let observedError;
  const page = loadTypeScript("app/assets/upload/page.tsx", {
    react: { ...React,
      useState: (initial) => { const index = cursor++; if (!(index in states)) states[index] = initial; return [states[index], (value) => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
      useRef: (initial) => refs[refCursor++] ||= { current: initial },
      useEffect: (run, deps) => { const index = effectCursor++; const old = effects[index]; if (!old || deps.some((d, i) => !Object.is(d, old.deps[i]))) { old?.cleanup?.(); effects[index] = { deps, run }; } },
    },
    "@/lib/hooks/use-idempotent-action": { useIdempotentAction: () => action },
    "@/lib/api/assets": { ...api,
      createUploadUrl: async () => { calls.push(["intent"]); return { asset_id: "asset", upload: { completed: !!options.replay } }; },
      uploadBytesToStorage: async () => { calls.push(["storage"]); },
      completeAsset: async (...args) => { calls.push(["complete", ...args]); return options.complete ? options.complete(...args) : asset(); },
      getAsset: async (...args) => { calls.push(["get", ...args]); return options.get ? options.get(...args) : asset(options.replayStatus ?? "VALIDATING"); },
    },
    "@tanstack/react-query": {
      useQueryClient: () => client,
      useQuery: (config) => {
        const index = queryCursor++;
        if (index === 0) return { data: options.policy, error: options.policyError, refetch: () => {} };
        if (index === 1) return { data: observed ?? config.initialData, error: observedError };
        return { data: { items: [], total: 0 } };
      },
      useInfiniteQuery: () => {
        const index = infiniteCursor++;
        return {
          data: { pages: [{ items: [{ id: index === 0 ? "project" : "product", name: "Test" }], total: 1, page: 1, page_size: 50 }] },
          isPending: false, isError: false, isFetching: false,
          hasNextPage: false, isFetchingNextPage: false, isFetchNextPageError: false,
          error: undefined, fetchNextPage: async () => {}, refetch: async () => {},
        };
      },
    },
  }).default;
  const originalWindow = global.window;
  global.window = { setTimeout: (fn, delay) => { delays.push(delay); timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id) };
  const render = () => {
    cursor = refCursor = effectCursor = queryCursor = infiniteCursor = 0;
    tree = page();
    for (const effect of effects) if (effect.run) { const run = effect.run; effect.run = null; effect.cleanup = run(); }
    return tree;
  };
  const find = (predicate) => { const found = nodes(tree).find(predicate); assert.ok(found); return found; };
  render();
  refs[0].current = { value: "", click: () => calls.push(["click"]) };
  return { render, calls, invalidations, states, timers, delays,
    phase: () => nodes(tree).some((node) => node.props?.title === "Hoàn thành") ? "done"
      : nodes(tree).some((node) => node.props?.title === "Lỗi tải lên") ? "error" : states[5],
    find, zone: () => find((node) => node.props?.role === "button"),
    picker: () => find((node) => node.type === "input" && node.props.type === "file"),
    observe: (data, error) => { observed = data; observedError = error; render(); render(); },
    start: async () => {
      find((node) => node.props.id === "project_select").props.onChange({ target: { value: "project" } }); render();
      await find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); render(); render();
    },
    dispose: () => { for (const effect of effects) effect.cleanup?.(); global.window = originalWindow; },
  };
}
const image = { name: "image.png", type: "image/png", size: 100, lastModified: 1 };

for (const request of ["get", "complete"]) {
  test(`${request} retry aborts at deadline, restores controls and can be retried`, async (t) => {
    let signal;
    const h = uploadHarness({ [request]: (...args) => {
      // Initial completion succeeds; only the explicit retry hangs.
      if (request === "complete" && !args[1].retry_validation) return asset();
      signal = args.at(-1);
      return new Promise((resolve, reject) => signal?.addEventListener("abort", () => reject(signal.reason), { once: true }));
    } });
    t.after(h.dispose);
    h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
    h.observe(asset(request === "complete" ? "FAILED" : "VALIDATING"), request === "get" ? new Error("offline") : undefined);
    const retry = () => h.find((n) => ["Thử xác thực lại", "Kiểm tra lại trạng thái"].includes(n.props?.children)).props.onClick();
    const pending = retry(); h.render();
    assert.equal(h.phase(), "checking");
    assert.ok(signal instanceof AbortSignal);
    assert.equal(h.zone().props["aria-disabled"], true);
    assert.equal(h.timers.size, 1);
    assert.equal(h.delays.at(-1), 120_000);
    [...h.timers.values()][0]();
    await pending; h.render();
    assert.equal(signal.aborted, true);
    assert.equal(h.phase(), "error");
    assert.equal(h.zone().props["aria-disabled"], false);
    assert.equal(h.invalidations.length, 0);
    assert.equal(h.timers.size, 0);
    const again = retry(); h.render();
    assert.equal(h.phase(), "checking");
    [...h.timers.values()][0](); await again;
  });
}

test("ordinary retry rejection restores controls and clears its deadline", async (t) => {
  const h = uploadHarness({ get: async () => { throw new Error("offline"); } }); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  h.observe(asset(), new Error("offline"));
  await h.find((n) => n.props?.children === "Kiểm tra lại trạng thái").props.onClick(); h.render();
  assert.equal(h.phase(), "error");
  assert.equal(h.zone().props["aria-disabled"], false);
  assert.equal(h.timers.size, 0);
});

test("accepted completion retry resumes GET polling and waits for READY", async (t) => {
  const h = uploadHarness(); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  h.observe(asset("FAILED"));
  await h.find((n) => n.props?.children === "Thử xác thực lại").props.onClick();
  const completion = h.calls.at(-1);
  assert.deepEqual(completion.slice(0, 3), ["complete", "asset", { retry_validation: true }]);
  assert.equal(completion[3].aborted, false);
  h.observe(asset());
  assert.equal(h.phase(), "validating");
  assert.equal(h.invalidations.length, 0);
  assert.equal(h.calls.filter(([kind]) => kind === "storage").length, 1);
  h.observe(asset("READY"));
  assert.equal(h.phase(), "done");
  assert.equal(h.invalidations.length, 2);
});

test("late response after timeout cannot overwrite a subsequent retry", async (t) => {
  const finish = [];
  const h = uploadHarness({ get: () => new Promise((resolve) => finish.push(resolve)) }); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  h.observe(asset(), new Error("offline"));
  const retry = () => h.find((n) => n.props?.children === "Kiểm tra lại trạng thái").props.onClick();
  const first = retry(); h.render(); [...h.timers.values()][0](); h.render();
  assert.equal(h.phase(), "error");
  const second = retry(); h.render();
  finish[0](asset("READY")); await first; h.render();
  assert.equal(h.phase(), "checking");
  finish[1](asset()); await second;
  h.observe(asset()); assert.equal(h.phase(), "validating");
  assert.equal(h.invalidations.length, 0);
});

test("unmount aborts a pending retry and ignores its late result", async () => {
  let signal, finish;
  const h = uploadHarness({ get: (...args) => { signal = args.at(-1); return new Promise((resolve) => { finish = resolve; }); } });
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  h.observe(asset(), new Error("offline"));
  const pending = h.find((n) => n.props?.children === "Kiểm tra lại trạng thái").props.onClick(); h.render();
  h.dispose();
  assert.equal(signal?.aborted, true);
  assert.equal(h.timers.size, 0);
  const state = [...h.states]; finish(asset("READY")); await pending;
  assert.deepEqual(h.states, state);
  assert.equal(h.invalidations.length, 0);
});

test("completion forwards cancellation and preserves the accepted validation DTO", async () => {
  let options;
  const accepted = asset();
  const api = loadTypeScript("lib/api/assets.ts", { "./client": { apiClient: { post: async (url, body, opts) => { options = opts; assert.deepEqual(body, { retry_validation: true }); return accepted; } } } });
  const controller = new AbortController();
  assert.equal(await api.completeAsset("asset", { retry_validation: true }, controller.signal), accepted);
  assert.equal(options.signal, controller.signal);
});

for (const file of ["app/assets/upload/page.tsx", "app/products/page.tsx", "app/videos/[videoId]/page.tsx"]) {
  test(`${file}: every native or labeled shared select has an associated label`, () => {
    const source = ts.createSourceFile(file, fs.readFileSync(path.join(__dirname, "..", file), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const labels = new Set(), controls = [];
    const attr = (node, name) => node.attributes.properties.find((a) => a.name?.getText(source) === name)?.initializer?.text;
    function visit(node) {
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        const tag = node.tagName.getText(source);
        if (tag === "label" && attr(node, "htmlFor")) labels.add(attr(node, "htmlFor"));
        if (tag === "select" || (tag === "Select" && attr(node, "label"))) controls.push(node);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
    assert.ok(controls.length);
    const ids = controls.map((c) => attr(c, "id"));
    assert.equal(new Set(ids).size, ids.length);
    for (const c of controls) {
      assert.ok(attr(c, "id"), c.getText(source));
      assert.ok(attr(c, "label") || labels.has(attr(c, "id")) || attr(c, "aria-label"), c.getText(source));
    }
  });
}

test("brand filter constrains intrinsic option width and permits wrapping", () => {
  const source = fs.readFileSync(path.join(__dirname, "../app/products/page.tsx"), "utf8");
  const select = source.match(/<select[\s\S]*?<\/select>/)[0];
  assert.match(select, /min-w-0/);
  assert.match(select, /w-full/);
  assert.match(select, /max-w-full/);
  assert.match(source, /grid grid-cols-1 sm:grid-cols-2 items-start/);
  assert.match(source, /min-w-0 space-y-1\.5/);
});
