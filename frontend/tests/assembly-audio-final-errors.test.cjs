const assert = require("node:assert/strict");
const test = require("node:test");
const React = require("react");
const { loadTypeScript } = require("./load-typescript.cjs");

function nodes(node) {
  return !node || typeof node !== "object" ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
}

function harness(video, api = {}) {
  const states = [];
  const refs = [];
  const queries = [];
  const mutations = [];
  const audioPages = api.audioPages ?? [];
  let hasMoreAudio = api.hasMoreAudio ?? false;
  let cursor = 0;
  let refCursor = 0;
  const mocks = {
    react: { ...React, use: (p) => p, useEffect: () => {}, useRef: (initial) => refs[refCursor++] ??= { current: initial }, useState: (initial) => {
      const index = cursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (value) => { states[index] = value; }];
    }, useRef: (value) => ({ current: value }), useCallback: (fn) => fn },
    "next/navigation": { useRouter: () => ({ push: () => {} }) },
    "@tanstack/react-query": {
      useQuery: (options) => { queries.push(options); return { data: options.queryKey.includes("final-versions") ? [{ id: "final", status: "READY", version_no: 1, created_at: "2026-10-07", assembly_config: {} }] : options.queryKey.includes("assets") ? undefined : video }; },
      useInfiniteQuery: (options) => { queries.push(options); return { data: { pages: audioPages }, hasNextPage: hasMoreAudio, isFetchingNextPage: false, fetchNextPage: async () => {
        const next = options.getNextPageParam(audioPages.at(-1), audioPages);
        if (next !== undefined) { audioPages.push(await options.queryFn({ pageParam: next })); hasMoreAudio = false; }
      } }; },
      useMutation: (options) => { mutations.push(options); return { isPending: false }; },
      useQueryClient: () => ({ invalidateQueries: () => {} }),
    },
    "react-hook-form": { useForm: () => ({ register: () => ({}), control: {}, handleSubmit: () => () => {}, reset: () => {}, formState: { errors: {}, isDirty: false } }), useWatch: () => "CUT" },
    "@/lib/hooks/use-video-events": { useVideoEvents: () => ({ assemblyProgress: {} }) },
    "@/lib/hooks/use-idempotent-action": { useIdempotentAction: () => ({}) },
    "@/lib/api/assets": { listAssets: api.listAssets },
    "@/lib/api/assembly": { listFinalVersions: () => {}, cancelFinalVersion: () => {}, downloadFinalVersion: api.downloadFinalVersion },
  };
  return { queries, mutations, mocks, render(page) { cursor = 0; refCursor = 0; queries.length = 0; mutations.length = 0; return page({ params: { videoId: "video" } }); } };
}

for (const product_id of [null, "product"]) test(`assembly audio query is READY AUDIO in current scope: ${product_id}`, async () => {
  const calls = [];
  const h = harness({ id: "video", project_id: "project", product_id, scenes: [] }, { listAssets: async (params) => calls.push(params) });
  const page = loadTypeScript("app/videos/[videoId]/assembly/page.tsx", h.mocks).default;
  h.render(page);
  const query = h.queries.find((q) => q.queryKey.includes("assets"));
  assert.equal(query.enabled, true);
  await query.queryFn({ pageParam: 1 });
  assert.equal(query.getNextPageParam({ items: [], total: 101, page: 1, page_size: 50 }), 2);
  await query.queryFn({ pageParam: 2 });
  assert.deepEqual(calls, [1, 2].map((page) => ({ size: 50, project_id: "project", ...(product_id ? { product_id } : {}), status: "READY", kind: "AUDIO", page })));
  assert.equal(query.initialPageParam, 1);
  assert.equal(query.getNextPageParam({ items: [], total: 101, page: 1, page_size: 50 }), 2);
  assert.deepEqual(query.queryKey.at(-1), { size: 50, project_id: "project", ...(product_id ? { product_id } : {}), status: "READY", kind: "AUDIO" });
});

test("assembly background audio picker keeps its first page and appends audio from the next page", async () => {
  const calls = [];
  const audio = (id) => ({ id, filename: `${id}.wav`, content_type: "audio/wav" });
  const firstPage = { items: [audio("audio-1")], total: 51, page: 1, page_size: 50 };
  const h = harness({ id: "video", project_id: "project", product_id: "product", scenes: [] }, {
    audioPages: [firstPage], hasMoreAudio: true,
    listAssets: async (params) => { calls.push(params); return { items: [audio("audio-2")], total: 51, page: 2, page_size: 50 }; },
  });
  const page = loadTypeScript("app/videos/[videoId]/assembly/page.tsx", h.mocks).default;
  let tree = h.render(page);
  const audioSelect = () => nodes(tree).find((node) => node.props?.id === "bg_audio");
  assert.ok(nodes(audioSelect()).some((node) => node.props?.value === "audio-1"));
  const loadMore = () => nodes(tree).find((node) => node.props?.onClick && node.props.children === "Tải thêm tệp âm thanh");
  await loadMore().props.onClick();
  tree = h.render(page);
  assert.ok(nodes(audioSelect()).some((node) => node.props?.value === "audio-1"));
  assert.ok(nodes(audioSelect()).some((node) => node.props?.value === "audio-2"));
  assert.equal(loadMore(), undefined);
  assert.deepEqual(calls, [{ size: 50, project_id: "project", product_id: "product", status: "READY", kind: "AUDIO", page: 2 }]);
});

test("assembly never requests global audio while video scope is loading", () => {
  const h = harness(undefined);
  const page = loadTypeScript("app/videos/[videoId]/assembly/page.tsx", h.mocks).default;
  h.render(page);
  assert.equal(h.queries.find((q) => q.queryKey.includes("assets")).enabled, false);
});

for (const [state, selection, fresh, label, disabled] of [
  ["fresh", "historical", true, "Sẵn sàng", false],
  ["stale", "historical", false, "Clip cần tạo lại", true],
  ["missing", null, false, "Thiếu clip", true],
]) test(`assembly distinguishes ${state} selection and gates submission`, () => {
  const scene = { id: "scene", enabled: true, scene_order: 0, duration_seconds: 5, prompt: "Scene", spec: {}, selected_generation_id: selection, selected_generation_fresh: fresh };
  const h = harness({ id: "video", project_id: "project", scenes: [scene] });
  const page = loadTypeScript("app/videos/[videoId]/assembly/page.tsx", h.mocks).default;
  const tree = h.render(page);
  assert.ok(nodes(tree).some((n) => n.type === "span" && n.props.children === label));
  const submit = nodes(tree).find((n) => n.props?.type === "submit");
  assert.equal(submit.props.disabled, disabled);
  assert.equal(scene.selected_generation_id, selection, "historical selection must remain unchanged");
  if (state === "stale") {
    assert.ok(!nodes(tree).some((n) => n.type === "span" && ["Ready", "Thiếu clip"].includes(n.props.children)));
  }
});

test("disabled stale scene does not prevent assembly of fresh enabled scenes", () => {
  const h = harness({ id: "video", project_id: "project", scenes: [
    { id: "fresh", enabled: true, duration_seconds: 5, selected_generation_id: "current", selected_generation_fresh: true },
    { id: "disabled", enabled: false, duration_seconds: 5, selected_generation_id: "historical", selected_generation_fresh: false },
  ] });
  const page = loadTypeScript("app/videos/[videoId]/assembly/page.tsx", h.mocks).default;
  assert.equal(nodes(h.render(page)).find((n) => n.props?.type === "submit").props.disabled, false);
});

test("final cancellation failure appears in app state and clears on retry and success", () => {
  const previousAlert = global.alert;
  const alerts = [];
  global.alert = (message) => alerts.push(message);
  try {
    const h = harness({ id: "video" });
    const page = loadTypeScript("app/videos/[videoId]/final-versions/page.tsx", h.mocks).default;
    h.render(page);
    h.mutations[0].onError(new Error("Cancellation failed"));
    assert.deepEqual(alerts, []);
    assert.ok(nodes(h.render(page)).some((n) => n.props?.variant === "destructive" && n.props.children === "Cancellation failed"));
    h.mutations[0].onMutate("final");
    assert.ok(!nodes(h.render(page)).some((n) => n.props?.children === "Cancellation failed"));
    h.mutations[0].onError(new Error("Second failure"));
    h.mutations[0].onSuccess();
    assert.ok(!nodes(h.render(page)).some((n) => n.props?.children === "Second failure"));
  } finally { global.alert = previousAlert; }
});

test("final download failure appears in app state and successful retry clears it", async () => {
  const previousWindow = global.window;
  const opened = [];
  const tabs = [];
  let fail = true;
  global.window = { open: (...args) => {
    opened.push(args);
    const tab = { closed: false, opener: {}, location: { replace: (url) => tab.url = url }, close() { this.closed = true; } };
    tabs.push(tab);
    return tab;
  } };
  try {
    const h = harness({ id: "video" }, { downloadFinalVersion: async () => {
      if (fail) throw new Error("Download failed");
      return { url: "https://example.test/final.mp4" };
    } });
    const page = loadTypeScript("app/videos/[videoId]/final-versions/page.tsx", h.mocks).default;
    const download = (tree) => nodes(tree).find((n) => n.props?.onClick && n.props.variant === "primary");
    const click = download(h.render(page)).props.onClick;
    const failedRequest = click();
    assert.deepEqual(opened, [["about:blank", "_blank"]], "tab must be reserved synchronously in the click gesture");
    await failedRequest;
    assert.equal(tabs[0].closed, true, "failed API request must close the reserved blank tab");
    assert.ok(nodes(h.render(page)).some((n) => n.props?.variant === "destructive" && n.props.children === "Download failed"));
    fail = false;
    await download(h.render(page)).props.onClick();
    assert.ok(!nodes(h.render(page)).some((n) => n.props?.children === "Download failed"));
    assert.deepEqual(opened, [["about:blank", "_blank"], ["about:blank", "_blank"]]);
    assert.equal(tabs[1].url, "https://example.test/final.mp4");
    assert.equal(tabs[1].opener, null);
  } finally { global.window = previousWindow; }
});

test("final download blocks duplicate clicks and reports popup blocking without requesting a URL", async () => {
  const previousWindow = global.window;
  let resolveDownload;
  let downloadCalls = 0;
  let openCalls = 0;
  global.window = { open: () => { openCalls += 1; return { closed: false, location: { replace() {} }, close() {} }; } };
  try {
    const h = harness({ id: "video" }, { downloadFinalVersion: () => {
      downloadCalls += 1;
      return new Promise((resolve) => { resolveDownload = resolve; });
    } });
    const page = loadTypeScript("app/videos/[videoId]/final-versions/page.tsx", h.mocks).default;
    const click = nodes(h.render(page)).find((n) => n.props?.onClick && n.props.variant === "primary").props.onClick;
    const first = click();
    const second = click();
    assert.equal(openCalls, 1);
    assert.equal(downloadCalls, 1);
    resolveDownload({ url: "https://example.test/final.mp4" });
    await Promise.all([first, second]);
  } finally { global.window = previousWindow; }

  const previousBlockedWindow = global.window;
  global.window = { open: () => null };
  try {
    let blockedRequests = 0;
    const h = harness({ id: "video" }, { downloadFinalVersion: async () => { blockedRequests += 1; return { url: "x" }; } });
    const page = loadTypeScript("app/videos/[videoId]/final-versions/page.tsx", h.mocks).default;
    const click = nodes(h.render(page)).find((n) => n.props?.onClick && n.props.variant === "primary").props.onClick;
    await click();
    assert.equal(blockedRequests, 0);
  } finally { global.window = previousBlockedWindow; }
});
