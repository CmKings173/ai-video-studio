const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { QueryClient, QueryObserver } = require("@tanstack/react-query");
const { loadTypeScript } = require("./load-typescript.cjs");

function apiHarness() {
  const calls = [];
  let response;
  const api = loadTypeScript("lib/api/assets.ts", {
    "./client": { apiClient: {
      get: async (...args) => { calls.push(["get", ...args]); return response; },
      post: async (...args) => { calls.push(["post", ...args]); return response; },
    } },
  });
  return { api, calls, respond: (value) => { response = value; } };
}
const asset = (status = "VALIDATING", phase = "QUEUED") => ({ id: "asset", status, media_metadata: { validation: { phase } } });

test("policy endpoint accepts safe config and rejects malformed config", async () => {
  const h = apiHarness();
  const policy = { max_upload_bytes: 1024, allowed_content_types: ["image/png"] };
  h.respond(policy);
  assert.equal(await h.api.getUploadPolicy(), policy);
  assert.equal(h.calls[0][1], "/api/v1/assets/upload-policy");
  for (const bad of [null, { ...policy, max_upload_bytes: 0 }, { ...policy, allowed_content_types: [] }, { ...policy, allowed_content_types: ["image/*"] }]) {
    h.respond(bad);
    await assert.rejects(h.api.getUploadPolicy(), /không hợp lệ/);
  }
});

test("shared file validation enforces count, known MIME, nonempty bytes and inclusive size limit", () => {
  const { api } = apiHarness();
  const policy = { max_upload_bytes: 100, allowed_content_types: ["image/png"] };
  const file = { type: "image/png", size: 100 };
  assert.equal(api.validateUploadFiles([file], policy), null);
  for (const files of [[], [file, file], [{ ...file, type: "" }], [{ ...file, type: "text/plain" }], [{ ...file, size: 0 }], [{ ...file, size: 101 }]]) {
    assert.equal(typeof api.validateUploadFiles(files, policy), "string");
  }
});

test("asset role compatibility matches server-enforced MIME rules and scope defaults", () => {
  const { api } = apiHarness();
  assert.equal(api.isAssetRoleCompatible("PRODUCT_IMAGE", "image/png"), true);
  assert.equal(api.isAssetRoleCompatible("PRODUCT_IMAGE", "video/mp4"), false);
  assert.equal(api.isAssetRoleCompatible("REFERENCE_VIDEO", "image/png"), false);
  assert.equal(api.isAssetRoleCompatible("BACKGROUND_AUDIO", "audio/wav"), true);
  assert.equal(api.isAssetRoleCompatible("PROJECT_REFERENCE", "image/png"), true);
  assert.equal(api.isAssetRoleCompatible("PROJECT_REFERENCE", "audio/wav"), false);
  assert.equal(api.isAssetRoleCompatible("SOURCE_VIDEO", "video/mp4"), true);
  assert.equal(api.isAssetRoleCompatible("SOURCE_VIDEO", "image/png"), false);
  assert.equal(api.isAssetRoleCompatible("REFERENCE_AUDIO", "audio/ogg"), true);
  assert.equal(api.isAssetRoleCompatible("SOURCE_VIDEO", "application/octet-stream"), false);
  assert.equal(api.isAssetRoleCompatible("UNKNOWN_ROLE", "image/png"), false);
  assert.equal(api.defaultAssetRole("image/png", "project"), "PROJECT_REFERENCE");
  assert.equal(api.defaultAssetRole("image/png", "product"), "PRODUCT_IMAGE");
  assert.equal(api.defaultAssetRole("video/mp4", "product"), "REFERENCE_VIDEO");
  assert.equal(api.defaultAssetRole("audio/wav", "project"), "BACKGROUND_AUDIO");
});

test("all upload roles use the exact supported MIME whitelist and fail closed for unknown values", () => {
  const { api } = apiHarness();
  const accepted = {
    PRODUCT_IMAGE: ["image/png", "image/jpeg", "image/webp"],
    PROJECT_REFERENCE: ["image/png", "image/jpeg", "image/webp"],
    SOURCE_VIDEO: ["video/mp4", "video/webm", "video/quicktime"],
    REFERENCE_VIDEO: ["video/mp4", "video/webm", "video/quicktime"],
    REFERENCE_AUDIO: ["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"],
    BACKGROUND_AUDIO: ["audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac"],
  };
  const allMimes = Object.values(accepted).flat();
  for (const [role, mimes] of Object.entries(accepted)) {
    for (const mime of allMimes) assert.equal(api.isAssetRoleCompatible(role, mime), mimes.includes(mime), `${role} with ${mime}`);
    assert.equal(api.isAssetRoleCompatible(role, "application/octet-stream"), false, `${role} rejects unlisted MIME`);
  }
  assert.equal(api.isAssetRoleCompatible("UNKNOWN_ROLE", "image/png"), false);
});

test("completion preserves HTTP202 VALIDATING DTO and explicit retry payload", async () => {
  const h = apiHarness(); h.respond(asset());
  assert.equal((await h.api.completeAsset("asset")).status, "VALIDATING");
  await h.api.completeAsset("asset", { retry_validation: true });
  assert.deepEqual(h.calls[1], ["post", "/api/v1/assets/asset/complete", { retry_validation: true }]);
});

test("polling continues QUEUED/RUNNING and stops on terminal, error, request budget and deadline", () => {
  const { api } = apiHarness();
  const started = Date.now();
  const options = api.assetValidationQueryOptions(asset(), started);
  const state = (data, count = 1, status = "success") => ({ state: { data, dataUpdateCount: count, status } });
  for (const phase of ["QUEUED", "RUNNING"]) assert.equal(options.refetchInterval(state(asset("VALIDATING", phase))), 2000);
  for (const status of ["READY", "FAILED"]) assert.equal(options.refetchInterval(state(asset(status))), false);
  assert.equal(options.refetchInterval(state(asset(), 60)), false);
  assert.equal(options.refetchInterval(state(asset(), 1, "error")), false);
  assert.equal(api.assetValidationQueryOptions(asset(), started - api.ASSET_VALIDATION_TIMEOUT_MS).refetchInterval(state(asset())), false);
  assert.equal(options.retry, false);
  assert.equal(options.refetchOnWindowFocus, false);
  assert.equal(options.refetchOnReconnect, false);
});

test("real TanStack observer unmount aborts its GET and removes polling", async () => {
  let signal;
  const api = loadTypeScript("lib/api/assets.ts", { "./client": { apiClient: { get: (_path, options) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
  } } } });
  const client = new QueryClient();
  const observer = new QueryObserver(client, api.assetValidationQueryOptions(asset(), Date.now()));
  const unsubscribe = observer.subscribe(() => {});
  assert.ok(signal);
  assert.equal(signal.aborted, false);
  unsubscribe();
  assert.equal(signal.aborted, true);
  client.clear();
});

test("direct storage POST retains presigned fields and puts file last", async (t) => {
  const originalFetch = global.fetch; t.after(() => { global.fetch = originalFetch; });
  let sent;
  global.fetch = async (...args) => { sent = args; return { ok: true }; };
  const { api } = apiHarness();
  const file = new File(["bytes"], "test.png", { type: "image/png" });
  await api.uploadBytesToStorage({ url: "https://storage.example/upload", fields: { key: "object", policy: "signed" } }, file);
  assert.equal(sent[0], "https://storage.example/upload");
  assert.equal(sent[1].method, "POST");
  assert.deepEqual(Array.from(sent[1].body.keys()), ["key", "policy", "file"]);
});

function nodes(node) {
  if (!node || typeof node !== "object") return [];
  return [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function text(node) {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join(" ");
  return React.Children.toArray(node.props?.children).map(text).join(" ");
}

function queryNotice(tree, title) {
  return nodes(tree).find((node) => node.type?.name === "QueryErrorNotice" && node.props.title === title);
}

function uploadHarness(options = {}) {
  options = { policy: { max_upload_bytes: 100, allowed_content_types: ["image/png"] }, ...options };
  const states = [], refs = [], effects = [];
  let cursor = 0, refCursor = 0, effectCursor = 0, queryCursor = 0, infiniteCursor = 0, tree;
  const loadedPages = [];
  const calls = [], invalidations = [], timers = new Map(); let timerId = 0;
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
      createUploadUrl: async (...args) => { calls.push(["intent", ...args]); return { asset_id: "asset", upload: { completed: !!options.replay } }; },
      downloadAsset: async (...args) => { calls.push(["download", ...args]); return options.downloadAsset ? options.downloadAsset(...args) : { url: "https://storage.invalid/asset" }; },
      uploadBytesToStorage: async () => { calls.push(["storage"]); },
      completeAsset: async (...args) => { calls.push(["complete", ...args]); return asset(); },
      getAsset: async () => { calls.push(["get"]); return asset(options.replayStatus ?? "VALIDATING"); },
    },
    "@/components/ui/button": { Button: ({ children, ...props }) => React.createElement("button", props, children) },
    "@/components/ui/select": { Select: ({ children, ...props }) => React.createElement("select", props, children) },
    "@/components/page-kit": {
      PageHeader: () => null,
      EmptyState: ({ title, detail }) => React.createElement("section", { "data-empty-state": title }, title, detail),
      StatusPill: ({ status }) => React.createElement("span", { "data-status": status }, status),
      QueryErrorNotice: ({ title, detail, onRetry, retryLabel = "Thử lại", isRetrying }) =>
        React.createElement("div", { role: "alert" }, title, React.createElement("p", null, detail),
          React.createElement("button", { disabled: isRetrying, onClick: onRetry }, retryLabel)),
    },
    "@tanstack/react-query": {
      useQueryClient: () => client,
      useQuery: (config) => {
        const index = queryCursor++;
        if (index === 0) return { data: options.policy, error: options.policyError, isPending: !options.policy && !options.policyError, isError: !!options.policyError, isFetching: !!options.fetching, refetch: () => { calls.push(["policy-retry"]); options.policy = { max_upload_bytes: 100, allowed_content_types: ["image/png"] }; options.policyError = undefined; } };
        if (index === 1) return { data: observed ?? config.initialData, error: observedError };
        const assets = Object.hasOwn(options, "assetsData") ? options.assetsData : { items: [], total: 0 };
        return {
          data: assets,
          isLoading: !!options.assetsLoading,
          isError: !!options.assetsError,
          isFetching: !!options.assetsFetching,
          refetch: options.refetchAssets ?? (async () => {
            calls.push(["assets-retry"]);
            options.assetsError = false;
            options.assetsData = { items: [], total: 0 };
          }),
        };
      },
      useInfiniteQuery: () => {
        const index = infiniteCursor++;
        const pages = (index === 0 ? options.projectPages : options.productPages) ?? [{
          items: [{ id: index === 0 ? "project" : "product", name: index === 0 ? "Project" : "Product" }],
          total: 1, page: 1, page_size: 50,
        }];
        const count = loadedPages[index] ?? 1;
        const visiblePages = pages.slice(0, count);
        return {
          data: { pages: visiblePages }, isPending: false, isError: false, isFetching: false,
          hasNextPage: visiblePages.length < pages.length, isFetchingNextPage: false, isFetchNextPageError: false,
          error: undefined,
          fetchNextPage: async () => { loadedPages[index] = Math.min(visiblePages.length + 1, pages.length); },
          refetch: async () => {},
        };
      },
    },
  }).default;
  const originalWindow = global.window;
  global.window = { setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; }, clearTimeout: (id) => timers.delete(id) };
  const render = () => {
    cursor = refCursor = effectCursor = queryCursor = infiniteCursor = 0;
    tree = page();
    for (const effect of effects) if (effect.run) { const run = effect.run; effect.run = null; effect.cleanup = run(); }
    return tree;
  };
  const find = (predicate) => { const found = nodes(tree).find(predicate); assert.ok(found); return found; };
  render();
  refs[0].current = { value: "", click: () => calls.push(["click"]) };
  return { render, calls, invalidations, states, timers,
    phase: () => nodes(tree).some((node) => node.props?.title === "Hoàn thành") ? "done"
      : nodes(tree).some((node) => node.props?.title === "Lỗi tải lên") ? "error" : states[5],
    find, zone: () => find((node) => node.props?.role === "button"),
    picker: () => find((node) => node.type === "input" && node.props.type === "file"),
    observe: (data, error) => { observed = data; observedError = error; render(); render(); },
    start: async (projectId = "project") => {
      find((node) => node.props.id === "project_select").props.onChange({ target: { value: projectId } }); render();
      await find((node) => node.type === "form").props.onSubmit({ preventDefault() {} }); render(); render();
    },
    dispose: () => { for (const effect of effects) effect.cleanup?.(); global.window = originalWindow; },
  };
}
const image = { name: "image.png", type: "image/png", size: 100, lastModified: 1 };

test("picker and drop reject the same files, clear errors, show drag feedback and support keyboard/click", (t) => {
  const h = uploadHarness({ policy: { max_upload_bytes: 100, allowed_content_types: ["image/png"] } }); t.after(h.dispose);
  for (const files of [[image, image], [{ ...image, type: "text/plain" }], [{ ...image, size: 101 }]]) {
    h.picker().props.onChange({ target: { files } }); h.render(); const error = h.states[6];
    h.zone().props.onDrop({ preventDefault() {}, dataTransfer: { files } }); h.render();
    assert.equal(h.states[6], error);
    assert.ok(error); assert.equal(h.states[0], null);
  }
  h.zone().props.onDragEnter({ preventDefault() {} }); h.render();
  assert.match(h.zone().props.className, /border-blue-500/);
  const transfer = {}; h.zone().props.onDragOver({ preventDefault() {}, dataTransfer: transfer });
  assert.equal(transfer.dropEffect, "copy");
  h.zone().props.onDrop({ preventDefault() {}, dataTransfer: { files: [image] } }); h.render();
  assert.equal(h.states[6], null); assert.equal(h.states[0], image);
  for (const key of ["Enter", " "]) h.zone().props.onKeyDown({ key, preventDefault() {} });
  h.zone().props.onClick(); assert.equal(h.calls.filter(([call]) => call === "click").length, 3);
});

test("upload role defaults follow scope, manual roles persist, and MIME changes reset invalid roles", (t) => {
  const h = uploadHarness({ policy: { max_upload_bytes: 100, allowed_content_types: ["image/png", "video/mp4"] } }); t.after(h.dispose);
  const role = () => h.find((node) => node.props.id === "asset_role");
  h.picker().props.onChange({ target: { files: [image] } }); h.render();
  assert.equal(role().props.value, "PROJECT_REFERENCE");
  h.find((node) => node.type === "button" && node.props.children === "Thuộc sản phẩm").props.onClick();
  h.render();
  assert.equal(role().props.value, "PRODUCT_IMAGE");

  role().props.onChange({ target: { value: "PROJECT_REFERENCE" } }); h.render();
  h.find((node) => node.type === "button" && node.props.children === "Thuộc dự án").props.onClick();
  h.render();
  assert.equal(role().props.value, "PROJECT_REFERENCE", "a manually selected server-valid role stays selected across scope changes");

  role().props.onChange({ target: { value: "PRODUCT_IMAGE" } }); h.render();
  h.picker().props.onChange({ target: { files: [{ name: "clip.mp4", type: "video/mp4", size: 100, lastModified: 2 }] } }); h.render();
  assert.equal(role().props.value, "REFERENCE_VIDEO");
  assert.ok(nodes(h.render()).some((node) => node.props?.role === "status" && /đã đặt lại/.test(node.props.children)));
  assert.equal(h.states[0].type, "video/mp4");
  assert.equal(h.find((node) => node.type === "option" && node.props.value === "PROJECT_REFERENCE").props.disabled, true);
  assert.equal(h.find((node) => node.type === "option" && node.props.value === "SOURCE_VIDEO").props.disabled, false);
});

test("upload project selector loads page two and submits the selected project ID", async (t) => {
  const pageOne = { items: Array.from({ length: 50 }, (_, i) => ({ id: `project-${i + 1}`, name: `Project ${i + 1}` })), total: 51, page: 1, page_size: 50 };
  const pageTwo = { items: [{ id: "project-51", name: "Project 51" }], total: 51, page: 2, page_size: 50 };
  const h = uploadHarness({ projectPages: [pageOne, pageTwo] }); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render();
  await h.find((node) => node.props.children === "Tải thêm dự án" && typeof node.props.onClick === "function").props.onClick();
  h.render();
  const projectSelect = h.find((node) => node.props.id === "project_select");
  assert.ok(React.Children.toArray(projectSelect.props.children).some((option) => option.props?.value === "project-51"));
  await h.start("project-51");
  assert.deepEqual(h.calls[0], ["intent", {
    filename: image.name,
    content_type: image.type,
    size_bytes: image.size,
    role: "PROJECT_REFERENCE",
    project_id: "project-51",
    product_id: null,
  }, "key"]);
});

test("asset download reserves the tab synchronously, closes it on failure, and navigates on retry", async (t) => {
  const originalWindow = global.window;
  const opened = [];
  const tabs = [];
  let requests = 0;
  const row = { id: "asset-1", filename: "render.mp4", content_type: "video/mp4", size_bytes: 1024, role: "GENERATED_VIDEO", status: "READY" };
  const h = uploadHarness({
    assetsData: { items: [row], total: 1 },
    downloadAsset: async () => {
      requests += 1;
      if (requests === 1) throw new Error("Expired presigned URL");
      return { url: "https://minio.example/render.mp4?signature=presigned" };
    },
  });
  const harnessWindow = global.window;
  global.window = { ...harnessWindow, open: (...args) => {
    opened.push(args);
    const tab = { closed: false, opener: {}, location: { replace(url) { tab.url = url; } }, close() { this.closed = true; } };
    tabs.push(tab);
    return tab;
  } };
  t.after(() => { h.dispose(); global.window = originalWindow; });

  let button = h.find((node) => node.props?.["aria-label"] === "Tải xuống render.mp4");
  const first = button.props.onClick();
  assert.deepEqual(opened, [["about:blank", "_blank"]], "reserve happens before awaiting the download endpoint");
  await first;
  assert.equal(tabs[0].closed, true);
  assert.equal(h.states[11], "Expired presigned URL");

  h.render();
  button = h.find((node) => node.props?.["aria-label"] === "Tải xuống render.mp4");
  await button.props.onClick();
  assert.deepEqual(opened, [["about:blank", "_blank"], ["about:blank", "_blank"]]);
  assert.equal(tabs[1].url, "https://minio.example/render.mp4?signature=presigned");
  assert.equal(tabs[1].opener, null);
  assert.equal(h.states[11], null);
});

test("existing asset inventory shows a retry on initial API failure instead of a fake empty count", async (t) => {
  const h = uploadHarness({ assetsData: undefined, assetsError: true }); t.after(h.dispose);
  let tree = h.render();
  const notice = queryNotice(tree, "Không thể tải danh sách tài nguyên");
  assert.ok(notice);
  assert.doesNotMatch(text(nodes(tree).find((node) => node.type === "h2")), /0/);
  assert.equal(nodes(tree).some((node) => node.type?.name === "EmptyState" && node.props.title === "Chưa có tài nguyên nào"), false);
  const retry = nodes(notice.type(notice.props)).find((node) => node.type === "button");
  assert.ok(retry);
  await retry.props.onClick();
  assert.deepEqual(h.calls, [["assets-retry"]]);
  tree = h.render();
  assert.equal(nodes(tree).some((node) => node.type?.name === "EmptyState" && node.props.title === "Chưa có tài nguyên nào"), true);
});

test("existing asset inventory marks cached rows stale after a refetch error and retries only that query", async (t) => {
  const row = { id: "asset-old", filename: "old-reference.png", content_type: "image/png", size_bytes: 128, role: "PROJECT_REFERENCE", status: "READY" };
  let retried = false;
  const h = uploadHarness({
    assetsData: { items: [row], total: 1 },
    assetsError: true,
    refetchAssets: async () => { retried = true; },
  }); t.after(h.dispose);
  const tree = h.render();
  const notice = queryNotice(tree, "Danh sách tài nguyên chưa được cập nhật");
  assert.ok(notice);
  assert.match(notice.props.detail, /dữ liệu đã tải trước đó/i);
  assert.equal(nodes(tree).some((node) => node.props?.children === row.filename), true);
  const retry = nodes(notice.type(notice.props)).find((node) => node.type === "button");
  assert.ok(retry);
  await retry.props.onClick();
  assert.equal(retried, true);
  assert.equal(h.calls.length, 0);
});

test("HTTP202 validation never claims success before READY; FAILED retries completion explicitly", async (t) => {
  const h = uploadHarness(); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  assert.equal(h.phase(), "validating"); assert.equal(h.invalidations.length, 0);
  assert.equal(h.find((node) => node.props.role === "status").props.children, "Đang kiểm tra tài nguyên…");
  assert.deepEqual(h.calls.slice(0, 3), [["intent", {
    filename: image.name, content_type: image.type, size_bytes: image.size,
    role: "PROJECT_REFERENCE", project_id: "project", product_id: null,
  }, "key"], ["storage"], ["complete", "asset", {}]]);
  h.observe(asset("VALIDATING", "RUNNING")); assert.equal(h.phase(), "validating");
  h.observe(asset("FAILED", "FAILED")); assert.equal(h.phase(), "error");
  await h.find((node) => node.props.children === "Thử xác thực lại").props.onClick();
  const retryCall = h.calls.at(-1);
  const signal = retryCall[3];
  assert.ok(signal instanceof AbortSignal);
  assert.equal(signal.aborted, false);
  assert.deepEqual(retryCall, ["complete", "asset", { retry_validation: true }, signal]);
  h.observe(asset()); assert.equal(h.phase(), "validating");
  h.observe(asset("READY", "READY")); assert.equal(h.phase(), "done");
  assert.equal(nodes(h.render()).some((node) => node.props?.children === image.name), false); assert.equal(h.invalidations.length, 2);
});

test("timeout and GET error stop validation without success and allow status checking", async (t) => {
  const h = uploadHarness(); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  [...h.timers.values()][0](); h.render();
  assert.equal(h.phase(), "error"); assert.equal(h.invalidations.length, 0);
  await h.find((node) => node.props.children === "Kiểm tra lại trạng thái").props.onClick();
  assert.deepEqual(h.calls.at(-1), ["get"]);
  h.observe(asset(), new Error("offline"));
  assert.equal(h.phase(), "error"); assert.equal(h.invalidations.length, 0);
});

test("completed upload replay GET still waits for READY and skips storage/completion", async (t) => {
  const h = uploadHarness({ replay: true }); t.after(h.dispose);
  h.picker().props.onChange({ target: { files: [image] } }); h.render(); await h.start();
  assert.deepEqual(h.calls, [["intent", {
    filename: image.name, content_type: image.type, size_bytes: image.size,
    role: "PROJECT_REFERENCE", project_id: "project", product_id: null,
  }, "key"], ["get"]]); assert.equal(h.phase(), "validating");
  h.observe(asset("READY")); assert.equal(h.phase(), "done");
});

function audioEditor(combinations, aggregateRequired, unqualified = false) {
  let queryCursor = 0;
  const scene = { id: "scene", enabled: true, scene_order: 0, spec: { continuity: "CUT" }, revision: 1, generation_config: {} };
  const scenes = aggregateRequired ? [scene, { ...scene, id: "next", scene_order: 1, spec: { continuity: "CONTINUOUS" } }] : [scene];
  const editor = loadTypeScript("components/generation/generation-editor.tsx", {
    react: { ...React, useState: (initial) => [typeof initial === "function" ? initial() : initial, () => {}] },
    "@tanstack/react-query": { useQueryClient: () => ({}), useQueries: () => [], useInfiniteQuery: () => ({ data: { pages: [{ items: [], total: 0, page: 1, page_size: 50 }] }, hasNextPage: false, isFetchingNextPage: false, fetchNextPage: async () => {} }), useQuery: () => queryCursor++ === 0
      ? { data: { available: true, combinations, ...(unqualified ? { qualified_capabilities: { combinations: [] } } : {}) } }
      : { data: undefined } },
  }).GenerationEditor;
  return nodes(editor({ scene, scenes, video: { id: "video", revision: 1, aspect_ratio: "9:16", project_id: "project" }, onClose() {}, onSubmit: async () => {}, isLoading: false }));
}
const capability = (scope, audioModes) => ({ workflow_id: `test-${scope}`, execution_scope: scope, mode: "t2v", quality_profile: "STANDARD", aspect_ratio: "9:16", required_asset_slots: [], max_reference_images: 0, max_reference_videos: 0, max_reference_audio: 0, max_total_reference_files: 0, audio_modes: audioModes });
for (const mode of ["mute", "source"]) {
  test(`${mode} uses qualified aggregate scope and standalone single scope`, () => {
    const combinations = [capability("aggregate", ["generate", mode]), capability("single_scene", ["generate"])];
    const option = (caps, aggregate, unqualified) => audioEditor(caps, aggregate, unqualified).find((node) => node.type === "option" && node.props.value === mode);
    assert.equal(option(combinations, true).props.disabled, false);
    assert.equal(option(combinations, false).props.disabled, true);
    const reversed = [capability("aggregate", ["generate"]), capability("single_scene", ["generate", mode])];
    assert.equal(option(reversed, true).props.disabled, true);
    assert.equal(option(reversed, false).props.disabled, false);
    assert.equal(option([capability("aggregate", ["generate", mode])], true).props.disabled, false);
    assert.equal(option([capability("single_scene", ["generate", mode])], true).props.disabled, true);
    assert.equal(option([capability("single_scene", ["generate", mode])], false).props.disabled, false);
    assert.equal(option(combinations, true, true).props.disabled, true);
  });
}


for (const failed of [false, true]) {
  test(`upload policy ${failed ? "error (including cached data)" : "pending"} rejects picker/drop/submit and retry restores real limits`, async (t) => {
    const h = uploadHarness(failed ? { policyError: new Error("offline") } : { policy: undefined }); t.after(h.dispose);
    for (const pick of [() => h.picker().props.onChange({ target: { files: [image] } }),
      () => h.zone().props.onDrop({ preventDefault() {}, dataTransfer: { files: [image] } })]) {
      pick(); h.render();
      assert.equal(h.states[0], null);
      assert.match(h.states[6], /Chưa tải được cấu hình tải lên/);
    }
    h.states[0] = image; h.render(); await h.start();
    assert.equal(h.calls.some(([call]) => call === "intent" || call === "storage"), false);
    assert.equal(h.find((node) => node.props.type === "submit").props.disabled, true);
    h.find((node) => node.props.children === "Tải lại cấu hình").props.onClick(); h.render();
    assert.deepEqual(h.calls, [["policy-retry"]]);
    h.picker().props.onChange({ target: { files: [{ ...image, size: 101 }] } }); h.render();
    assert.equal(h.states[0], null);
    h.zone().props.onDrop({ preventDefault() {}, dataTransfer: { files: [image] } }); h.render(); await h.start();
    assert.equal(h.calls.some(([call]) => call === "intent"), true);
  });
}
