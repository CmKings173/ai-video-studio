const assert = require("node:assert/strict");
const test = require("node:test");
const { loadTypeScript } = require("./load-typescript.cjs");

function harness({ visible = true, queries = [] } = {}) {
  let effect;
  let cleanup;
  const stateWrites = [];
  const listeners = new Map();
  const intervals = new Map();
  const invalidations = [];
  const querySubscribers = new Set();
  const states = [];
  let intervalId = 0;
  const queryCache = {
    getAll: () => queries,
    subscribe: (listener) => { querySubscribers.add(listener); return () => querySubscribers.delete(listener); },
  };
  const queryClient = {
    getQueryCache: () => queryCache,
    getQueryData: (key) => queries.find((query) => JSON.stringify(query.queryKey) === JSON.stringify(key))?.state?.data,
    invalidateQueries: async (filters) => { invalidations.push(filters); },
  };
  class FakeEventSource {
    constructor(url, options) { this.url = url; this.options = options; this.events = new Map(); this.closed = false; harness.eventSource = this; }
    addEventListener(type, listener) { this.events.set(type, listener); }
    removeEventListener(type, listener) { if (this.events.get(type) === listener) this.events.delete(type); }
    close() { this.closed = true; }
    emit(type, data) { this.events.get(type)?.({ data: JSON.stringify(data) }); }
  }
  const original = {
    EventSource: global.EventSource,
    document: global.document,
    setInterval: global.setInterval,
    clearInterval: global.clearInterval,
  };
  global.EventSource = FakeEventSource;
  global.document = {
    visibilityState: visible ? "visible" : "hidden",
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); },
  };
  global.setInterval = (callback, ms) => { const id = ++intervalId; intervals.set(id, { callback, ms }); return id; };
  global.clearInterval = (id) => intervals.delete(id);

  let stateCursor = 0;
  const react = {
    useState: (initial) => {
      const index = stateCursor++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (next) => {
        const value = typeof next === "function" ? next(states[index]) : next;
        states[index] = value;
        stateWrites.push({ index, value });
      }];
    },
    useRef: (initial) => ({ current: initial }),
    useEffect: (callback) => { effect = callback; },
  };
  const { useVideoEvents } = loadTypeScript("lib/hooks/use-video-events.ts", {
    react,
    "@tanstack/react-query": { useQueryClient: () => queryClient },
    "../api/client": { apiUrl: (path) => path },
    "../query/query-keys": { queryKeys: {
      videos: {
        detail: (id) => ["videos", "detail", id],
        all: ["videos"],
        scenes: (id) => ["videos", id, "scenes"],
        finalVersions: (id) => ["videos", id, "final-versions"],
      },
      scenes: { detail: (id) => ["scenes", "detail", id], generations: (id) => ["scenes", id, "generations"] },
      generations: { detail: (id) => ["generations", "detail", id] },
      dashboard: { summary: ["dashboard", "summary"] },
    } },
  });

  const render = (videoId = "video-1") => {
    stateCursor = 0;
    const result = useVideoEvents(videoId);
    cleanup = effect?.();
    return result;
  };
  const flush = async () => { await new Promise((resolve) => setImmediate(resolve)); };
  const restore = () => {
    if (original.EventSource === undefined) delete global.EventSource; else global.EventSource = original.EventSource;
    if (original.document === undefined) delete global.document; else global.document = original.document;
    global.setInterval = original.setInterval;
    global.clearInterval = original.clearInterval;
  };
  return {
    render, flush, restore, intervals, invalidations, listeners, querySubscribers, stateWrites,
    get eventSource() { return harness.eventSource; },
    triggerVisibility: (state) => { global.document.visibilityState = state; listeners.get("visibilitychange")?.(); },
    setQueries: (next) => { queries = next; for (const listener of querySubscribers) listener({ type: "updated" }); },
    cleanup: () => cleanup?.(),
  };
}

const sceneQueries = (status) => [
  { queryKey: ["videos", "video-1", "scenes"], state: { data: { items: [{ id: "scene-1" }] } } },
  { queryKey: ["scenes", "scene-1", "generations", { page: 1 }], state: { data: { items: [{ id: "gen-1", status }] } } },
];

test("disconnected SSE immediately resyncs and polls active work every 12 seconds", async () => {
  const h = harness({ queries: sceneQueries("RUNNING") });
  try {
    h.render();
    h.eventSource.onerror();
    await h.flush();
    assert.ok(h.invalidations.length >= 3);
    assert.equal(h.intervals.size, 1);
    assert.equal([...h.intervals.values()][0].ms, 12_000);
  } finally { h.cleanup(); h.restore(); }
});

test("disconnected SSE does not poll when all cached work is terminal", async () => {
  const h = harness({ queries: sceneQueries("COMPLETED") });
  try {
    h.render();
    h.eventSource.onerror();
    await h.flush();
    assert.ok(h.invalidations.length >= 3);
    assert.equal(h.intervals.size, 0);
  } finally { h.cleanup(); h.restore(); }
});

test("a missed completion found by fallback resync stops further polling", async () => {
  const h = harness({ queries: sceneQueries("RUNNING") });
  try {
    h.render();
    h.eventSource.onerror();
    await h.flush();
    assert.equal(h.intervals.size, 1);
    h.setQueries(sceneQueries("COMPLETED"));
    assert.equal(h.intervals.size, 0);
  } finally { h.cleanup(); h.restore(); }
});

test("SSE terminal events keep UI progress terminal instead of retaining RUNNING", () => {
  const h = harness();
  try {
    h.render();
    h.eventSource.emit("generation.progress", { generation_id: "gen-1", scene_id: "scene-1", status: "RUNNING", progress: 0.8 });
    h.eventSource.emit("generation.completed", { generation_id: "gen-1", scene_id: "scene-1", progress: 1 });
    const generationWrites = h.stateWrites.filter((write) => write.index === 1);
    assert.equal(generationWrites.at(-2).value.value["gen-1"].status, "RUNNING");
    assert.equal(generationWrites.at(-1).value.value["gen-1"].status, "COMPLETED");
  } finally { h.cleanup(); h.restore(); }
});

test("reconnect performs a fresh sync and stops the fallback timer", async () => {
  const h = harness({ queries: sceneQueries("RUNNING") });
  try {
    h.render();
    h.eventSource.onerror();
    await h.flush();
    const beforeReconnect = h.invalidations.length;
    assert.equal(h.intervals.size, 1);
    h.eventSource.onopen();
    await h.flush();
    assert.ok(h.invalidations.length > beforeReconnect);
    assert.equal(h.intervals.size, 0);
  } finally { h.cleanup(); h.restore(); }
});

test("polling pauses in a hidden tab, resumes with an immediate sync, and cleans up on unmount", async () => {
  const h = harness({ visible: false, queries: sceneQueries("RUNNING") });
  try {
    h.render();
    h.eventSource.onerror();
    await h.flush();
    assert.equal(h.intervals.size, 0);
    h.triggerVisibility("visible");
    await h.flush();
    assert.equal(h.intervals.size, 1);
    const beforeHiddenTick = h.invalidations.length;
    const timer = [...h.intervals.values()][0];
    h.triggerVisibility("hidden");
    await timer.callback();
    assert.equal(h.invalidations.length, beforeHiddenTick);
    h.cleanup();
    assert.equal(h.intervals.size, 0);
    assert.equal(h.eventSource.closed, true);
    assert.equal(h.listeners.size, 0);
    assert.equal(h.querySubscribers.size, 0);
  } finally { h.cleanup(); h.restore(); }
});
