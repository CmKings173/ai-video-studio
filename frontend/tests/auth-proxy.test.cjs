const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { loadTypeScript } = require("./load-typescript.cjs");

const ingressToken = "ingress-test-credential-32-characters";
const proxyToken = "internal-test-credential-32-characters";
const maxProxyRequestBytes = 2 * 1024 * 1024;

function proxyRequest(method, body, headers = {}) {
  const init = { method, headers };
  if (body !== undefined) {
    init.body = body;
    if (body instanceof ReadableStream) init.duplex = "half";
  }
  const request = new Request("http://studio/api/v1/proxy-test", init);
  request.nextUrl = new URL(request.url);
  return request;
}

async function invokeProxy(method, request, fetchImplementation, requestMaxBytes = maxProxyRequestBytes) {
  const previous = {
    fetch: global.fetch,
    ingress: process.env.STUDIO_INGRESS_TOKEN,
    proxy: process.env.INTERNAL_PROXY_TOKEN,
    requestMaxBytes: process.env.REQUEST_BODY_MAX_BYTES,
  };
  process.env.STUDIO_INGRESS_TOKEN = ingressToken;
  process.env.INTERNAL_PROXY_TOKEN = proxyToken;
  process.env.REQUEST_BODY_MAX_BYTES = String(requestMaxBytes);
  global.fetch = fetchImplementation;
  try {
    const handlers = loadTypeScript("app/api/[...path]/route.ts");
    return await handlers[method](request, {
      params: Promise.resolve({ path: ["v1", "proxy-test"] }),
    });
  } finally {
    global.fetch = previous.fetch;
    for (const [name, value] of [
      ["STUDIO_INGRESS_TOKEN", previous.ingress],
      ["INTERNAL_PROXY_TOKEN", previous.proxy],
      ["REQUEST_BODY_MAX_BYTES", previous.requestMaxBytes],
    ]) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
  }
}

test("backend, frontend, and ingress use one configurable request-body limit", () => {
  const root = path.resolve(__dirname, "../..");
  const compose = fs.readFileSync(path.join(root, "infra/compose.yaml"), "utf8");
  const ingress = fs.readFileSync(path.join(root, "infra/ingress/default.conf.template"), "utf8");
  const frontendRoute = fs.readFileSync(path.join(root, "frontend/app/api/[...path]/route.ts"), "utf8");

  assert.equal((compose.match(/REQUEST_BODY_MAX_BYTES: \$\{REQUEST_BODY_MAX_BYTES:-2097152\}/g) || []).length, 3);
  assert.match(ingress, /client_max_body_size \$\{REQUEST_BODY_MAX_BYTES\};/);
  assert.match(frontendRoute, /process\.env\.REQUEST_BODY_MAX_BYTES/);
  assert.match(compose, /PROXY_REQUEST_BODY_TIMEOUT_MS: \$\{PROXY_REQUEST_BODY_TIMEOUT_MS:-60000\}/);
});

test("proxy follows the shared configured byte limit", async () => {
  const previousLimit = process.env.REQUEST_BODY_MAX_BYTES;
  process.env.REQUEST_BODY_MAX_BYTES = "4096";
  try {
    const accepted = proxyRequest("POST", "x".repeat(3072));
    const acceptedResponse = await invokeProxy("POST", accepted, async (_url, options) => {
      const reader = options.body.getReader();
      let received = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        received += value.byteLength;
      }
      assert.equal(received, 3072);
      return new Response("ok");
    }, 4096);
    assert.equal(acceptedResponse.status, 200);

    const rejected = proxyRequest("POST", "tiny", { "content-length": "4097" });
    let fetchCalled = false;
    const rejectedResponse = await invokeProxy("POST", rejected, async () => {
      fetchCalled = true;
      return new Response("unexpected");
    }, 4096);
    assert.equal(rejectedResponse.status, 413);
    assert.equal(fetchCalled, false);
  } finally {
    if (previousLimit === undefined) delete process.env.REQUEST_BODY_MAX_BYTES;
    else process.env.REQUEST_BODY_MAX_BYTES = previousLimit;
  }
});

async function forwarded(headers, configured = true) {
  const previous = { fetch: global.fetch, ingress: process.env.STUDIO_INGRESS_TOKEN, proxy: process.env.INTERNAL_PROXY_TOKEN };
  if (configured) {
    process.env.STUDIO_INGRESS_TOKEN = ingressToken;
    process.env.INTERNAL_PROXY_TOKEN = proxyToken;
  } else {
    delete process.env.STUDIO_INGRESS_TOKEN;
    delete process.env.INTERNAL_PROXY_TOKEN;
  }
  let sent;
  global.fetch = async (_url, options) => { sent = options.headers; return new Response("ok"); };
  try {
    const { POST } = loadTypeScript("app/api/[...path]/route.ts");
    const request = new Request("http://studio/api/v1/auth/login", { method: "POST", headers, body: "{}" });
    request.nextUrl = new URL(request.url);
    assert.equal((await POST(request, { params: Promise.resolve({ path: ["v1", "auth", "login"] }) })).status, 200);
    return sent;
  } finally {
    global.fetch = previous.fetch;
    for (const [name, value] of [["STUDIO_INGRESS_TOKEN", previous.ingress], ["INTERNAL_PROXY_TOKEN", previous.proxy]]) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
  }
}

test("authenticated ingress IP reaches backend using separate internal credentials", async () => {
  const sent = await forwarded({ "x-studio-ingress-token": ingressToken, "x-studio-client-ip": "2001:db8::1", "x-forwarded-for": "spoof", "cookie": "session=test" });
  assert.equal(sent.get("x-studio-client-ip"), "2001:db8::1");
  assert.equal(sent.get("x-studio-proxy-token"), proxyToken);
  assert.equal(sent.get("cookie"), "session=test");
  assert.equal(sent.get("x-studio-ingress-token"), null);
  assert.equal(sent.get("x-forwarded-for"), null);
});

test("rejects an oversized declared body before contacting the backend", async () => {
  let fetchCalled = false;
  const request = proxyRequest("POST", "tiny", {
    "content-length": String(maxProxyRequestBytes + 1),
  });

  const response = await invokeProxy("POST", request, async () => {
    fetchCalled = true;
    return new Response("unexpected");
  });

  assert.equal(response.status, 413);
  assert.equal(fetchCalled, false);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const payload = await response.json();
  assert.equal(payload.error.code, "REQUEST_BODY_TOO_LARGE");
  assert.equal(typeof payload.error.trace_id, "string");
  assert.deepEqual(payload.error.details, {});
});

for (const [label, headers] of [
  ["missing Content-Length", {}],
  ["spoofed small Content-Length", { "content-length": "1" }],
]) {
  test(`streams and rejects actual request bytes over the limit with ${label}`, async () => {
    let pulls = 0;
    let sourceCancelled = false;
    let releasePendingPull;
    let forwardedBytes = 0;
    let upstreamAborted = false;
    const body = new ReadableStream({
      pull(controller) {
        pulls += 1;
        if (pulls <= 2) {
          controller.enqueue(new Uint8Array(1024 * 1024));
        } else if (pulls === 3) {
          controller.enqueue(new Uint8Array(1));
        } else {
          return new Promise((resolve) => { releasePendingPull = resolve; });
        }
      },
      cancel() {
        sourceCancelled = true;
        releasePendingPull?.();
      },
    });
    const request = proxyRequest("POST", body, headers);

    const response = await invokeProxy("POST", request, async (_url, options) => {
      if (options.body instanceof ReadableStream) {
        const reader = options.body.getReader();
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            forwardedBytes += value.byteLength;
          }
        } catch {
          // The proxy aborts the upstream request when its byte limit is crossed.
        }
        upstreamAborted = options.signal.aborted;
      }
      return new Response("unexpected");
    });

    assert.equal(response.status, 413);
    assert.ok(forwardedBytes <= maxProxyRequestBytes);
    assert.equal(upstreamAborted, true);
    assert.equal(sourceCancelled, true);
  });
}

test("bounds parallel streamed bodies independently", async () => {
  const concurrency = 4;
  const chunkBytes = 512 * 1024;
  const previous = {
    fetch: global.fetch,
    ingress: process.env.STUDIO_INGRESS_TOKEN,
    proxy: process.env.INTERNAL_PROXY_TOKEN,
    requestMaxBytes: process.env.REQUEST_BODY_MAX_BYTES,
  };
  process.env.STUDIO_INGRESS_TOKEN = ingressToken;
  process.env.INTERNAL_PROXY_TOKEN = proxyToken;
  process.env.REQUEST_BODY_MAX_BYTES = String(maxProxyRequestBytes);

  let fetchCount = 0;
  let activeFetches = 0;
  let maximumActiveFetches = 0;
  let releaseAllFetches;
  const allFetchesStarted = new Promise((resolve) => { releaseAllFetches = resolve; });
  const forwardedBytes = Array(concurrency).fill(0);
  const upstreamAborted = Array(concurrency).fill(false);
  const sourceCancelled = Array(concurrency).fill(false);
  global.fetch = async (_url, options) => {
    const index = fetchCount++;
    activeFetches += 1;
    maximumActiveFetches = Math.max(maximumActiveFetches, activeFetches);
    if (fetchCount === concurrency) releaseAllFetches();
    await allFetchesStarted;
    try {
      const reader = options.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        forwardedBytes[index] += value.byteLength;
      }
    } catch {
      // Each oversized stream aborts its own upstream request.
    } finally {
      upstreamAborted[index] = options.signal.aborted;
      activeFetches -= 1;
    }
    return new Response("unexpected");
  };

  try {
    const handlers = loadTypeScript("app/api/[...path]/route.ts");
    const requests = Array.from({ length: concurrency }, (_, index) => {
      let chunks = 0;
      const body = new ReadableStream({
        pull(controller) {
          if (chunks === 8) {
            controller.close();
            return;
          }
          chunks += 1;
          controller.enqueue(new Uint8Array(chunkBytes));
        },
        cancel() {
          sourceCancelled[index] = true;
        },
      });
      return proxyRequest("POST", body);
    });
    const responses = await Promise.all(requests.map((request) => handlers.POST(request, {
      params: Promise.resolve({ path: ["v1", "proxy-test"] }),
    })));

    assert.deepEqual(responses.map((response) => response.status), Array(concurrency).fill(413));
    assert.equal(maximumActiveFetches, concurrency);
    assert.deepEqual(forwardedBytes, Array(concurrency).fill(maxProxyRequestBytes));
    assert.deepEqual(upstreamAborted, Array(concurrency).fill(true));
    assert.deepEqual(sourceCancelled, Array(concurrency).fill(true));
  } finally {
    global.fetch = previous.fetch;
    for (const [name, value] of [
      ["STUDIO_INGRESS_TOKEN", previous.ingress],
      ["INTERNAL_PROXY_TOKEN", previous.proxy],
      ["REQUEST_BODY_MAX_BYTES", previous.requestMaxBytes],
    ]) {
      if (value === undefined) delete process.env[name]; else process.env[name] = value;
    }
  }
});

test("checks the remaining request stream before returning an early upstream response", async () => {
  let pulls = 0;
  let sourceCancelled = false;
  let releasePendingPull;
  let upstreamResponseCancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      pulls += 1;
      if (pulls <= 2) {
        controller.enqueue(new Uint8Array(1024 * 1024));
      } else if (pulls === 3) {
        controller.enqueue(new Uint8Array(1));
      } else {
        return new Promise((resolve) => { releasePendingPull = resolve; });
      }
    },
    cancel() {
      sourceCancelled = true;
      releasePendingPull?.();
    },
  });
  const request = proxyRequest("POST", body);
  const upstreamBody = new ReadableStream({
    pull(controller) {
      controller.enqueue(new TextEncoder().encode("early response"));
    },
    cancel() {
      upstreamResponseCancelled = true;
    },
  });

  const response = await invokeProxy("POST", request, async () => new Response(upstreamBody));

  assert.equal(response.status, 413);
  assert.equal(upstreamResponseCancelled, true);
  assert.equal(sourceCancelled, true);
});

test("preserves request identity and streams the upstream response", async () => {
  let sent;
  let releaseSecondChunk;
  let producedSecondChunk = false;
  const secondChunkReady = new Promise((resolve) => { releaseSecondChunk = resolve; });
  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode("first"));
    },
    async pull(controller) {
      if (producedSecondChunk) return;
      producedSecondChunk = true;
      await secondChunkReady;
      controller.enqueue(encoder.encode("second"));
      controller.close();
    },
  });
  const request = proxyRequest("PUT", new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode("request-body"));
      controller.close();
    },
  }), {
    "content-type": "application/json",
    cookie: "session=kept",
    "x-csrf-token": "csrf-value",
    "x-studio-ingress-token": ingressToken,
    "x-studio-client-ip": "192.0.2.7",
  });

  try {
    const response = await invokeProxy("PUT", request, async (_url, options) => {
      sent = options;
      const reader = options.body.getReader();
      while (!(await reader.read()).done) {}
      const headers = new Headers({ "x-proxy-test": "streamed" });
      headers.append("set-cookie", "first=one; Path=/; HttpOnly");
      headers.append("set-cookie", "second=two; Path=/; HttpOnly");
      return new Response(upstreamBody, { headers });
    });

    assert.equal(sent.method, "PUT");
    assert.equal(sent.duplex, "half");
    assert.equal(sent.headers.get("content-type"), "application/json");
    assert.equal(sent.headers.get("cookie"), "session=kept");
    assert.equal(sent.headers.get("x-csrf-token"), "csrf-value");
    assert.equal(sent.headers.get("x-studio-client-ip"), "192.0.2.7");
    assert.equal(sent.headers.get("x-studio-proxy-token"), proxyToken);
    assert.equal(response.headers.get("x-proxy-test"), "streamed");
    assert.deepEqual(response.headers.getSetCookie(), [
      "first=one; Path=/; HttpOnly",
      "second=two; Path=/; HttpOnly",
    ]);

    const reader = response.body.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "first");
    releaseSecondChunk();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "second");
    assert.equal((await reader.read()).done, true);
  } finally {
    releaseSecondChunk();
  }
});

for (const [label, headers, configured] of [
  ["unconfigured trust", { "x-studio-ingress-token": ingressToken, "x-studio-client-ip": "192.0.2.1" }, false],
  ["browser spoof", { "x-studio-client-ip": "192.0.2.1", "x-studio-proxy-token": proxyToken, "x-forwarded-for": "192.0.2.1" }, true],
  ["wrong credential", { "x-studio-ingress-token": "wrong", "x-studio-client-ip": "192.0.2.1" }, true],
  ["address chain", { "x-studio-ingress-token": ingressToken, "x-studio-client-ip": "192.0.2.1, 192.0.2.2" }, true],
  ["malformed address", { "x-studio-ingress-token": ingressToken, "x-studio-client-ip": "not-an-ip" }, true],
]) {
  test(`${label} cannot forward a client identity`, async () => {
    const sent = await forwarded(headers, configured);
    assert.equal(sent.get("x-studio-client-ip"), null);
    assert.equal(sent.get("x-studio-proxy-token"), null);
  });
}

async function withinBodyDeadline(promise, milliseconds = 250) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`proxy did not settle within ${milliseconds}ms`)), milliseconds);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

for (const [label, fetchImplementation] of [
  ["early 422", async () => new Response("invalid", { status: 422 })],
  ["backend connection failure", async () => { throw new TypeError("fetch failed"); }],
]) {
  test(`slow-client reproduction: ${label} cannot wait indefinitely for an open body`, async () => {
    const previousTimeout = process.env.PROXY_REQUEST_BODY_TIMEOUT_MS;
    process.env.PROXY_REQUEST_BODY_TIMEOUT_MS = "40";
    let source;
    const body = new ReadableStream({
      start(controller) {
        source = controller;
        controller.enqueue(new TextEncoder().encode("first chunk"));
      },
    });
    const pending = invokeProxy("POST", proxyRequest("POST", body), fetchImplementation);
    try {
      const response = await withinBodyDeadline(pending);
      assert.equal(response.status, 408);
      assert.equal((await response.json()).error.code, "REQUEST_BODY_TIMEOUT");
    } finally {
      try { source.close(); } catch { /* The deadline may already have cancelled the source. */ }
      await withinBodyDeadline(pending).catch(() => {});
      if (previousTimeout === undefined) delete process.env.PROXY_REQUEST_BODY_TIMEOUT_MS;
      else process.env.PROXY_REQUEST_BODY_TIMEOUT_MS = previousTimeout;
    }
  });
}

// Keep one environment/fetch override for a whole scenario, including concurrent requests.
async function withSlowClientProxy(run, options = {}) {
  const { maxBytes = 1024, fetch } = options;
  const timeout = Object.hasOwn(options, "timeout") ? options.timeout : "40";
  const environment = {
    STUDIO_INGRESS_TOKEN: ingressToken,
    INTERNAL_PROXY_TOKEN: proxyToken,
    REQUEST_BODY_MAX_BYTES: String(maxBytes),
    PROXY_REQUEST_BODY_TIMEOUT_MS: timeout,
  };
  const previousEnvironment = Object.fromEntries(Object.keys(environment).map((key) => [key, process.env[key]]));
  const previousFetch = global.fetch;
  const previousSetTimeout = global.setTimeout;
  const previousClearTimeout = global.clearTimeout;
  const timers = new Set();
  const delays = [];
  const sources = [];
  const pending = [];
  for (const [key, value] of Object.entries(environment)) {
    if (value === undefined) delete process.env[key]; else process.env[key] = value;
  }
  global.fetch = fetch || (async () => new Response("invalid", { status: 422 }));
  const handlers = loadTypeScript("app/api/[...path]/route.ts");
  global.setTimeout = (callback, delay, ...args) => {
    delays.push(delay);
    let timer;
    timer = previousSetTimeout(() => {
      timers.delete(timer);
      callback(...args);
    }, delay);
    timers.add(timer);
    return timer;
  };
  global.clearTimeout = (timer) => {
    timers.delete(timer);
    return previousClearTimeout(timer);
  };
  const harness = {
    timers,
    delays,
    openBody(cancel = () => {}) {
      let controller;
      const body = new ReadableStream({
        start(source) {
          controller = source;
          source.enqueue(new Uint8Array([1]));
        },
        cancel,
      });
      const source = {
        body,
        enqueue(bytes = new Uint8Array([2])) { controller.enqueue(bytes); },
        close() { try { controller.close(); } catch { /* Already cancelled. */ } },
        error(error) { try { controller.error(error); } catch { /* Already terminal. */ } },
      };
      sources.push(source);
      return source;
    },
    call(request) {
      const result = handlers[request.method](request, { params: Promise.resolve({ path: ["v1", "proxy-test"] }) });
      // Attach a rejection handler immediately, including when a bounded assertion fails.
      result.catch(() => {});
      pending.push(result);
      return result;
    },
  };
  try {
    await run(harness);
    assert.equal(timers.size, 0, "body timers must be cleared after terminal completion");
  } finally {
    for (const source of sources) source.close();
    await withinBodyDeadline(Promise.allSettled(pending)).catch(() => {});
    for (const timer of timers) previousClearTimeout(timer);
    global.setTimeout = previousSetTimeout;
    global.clearTimeout = previousClearTimeout;
    global.fetch = previousFetch;
    for (const [key, value] of Object.entries(previousEnvironment)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  }
}

async function assertProxyFailure(response, status, code) {
  assert.equal(response.status, status);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const payload = await response.json();
  assert.equal(payload.error.code, code);
  assert.equal(typeof payload.error.trace_id, "string");
  assert.deepEqual(payload.error.details, {});
}

function observeAbortListeners(signal) {
  const listeners = new Set();
  const add = signal.addEventListener.bind(signal);
  const remove = signal.removeEventListener.bind(signal);
  signal.addEventListener = (type, listener, options) => {
    if (type === "abort") listeners.add(listener);
    return add(type, listener, options);
  };
  signal.removeEventListener = (type, listener, options) => {
    if (type === "abort") listeners.delete(listener);
    return remove(type, listener, options);
  };
  return listeners;
}

const bodyPause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

test("slow-client total deadline expires despite continuous small chunks", async () => {
  let sourceCancelled = false;
  let upstreamSignal;
  let chunks = 0;
  await withSlowClientProxy(async (h) => {
    const source = h.openBody(() => { sourceCancelled = true; });
    const request = proxyRequest("POST", source.body);
    const listeners = observeAbortListeners(request.signal);
    const trickle = setInterval(() => {
      try { source.enqueue(); chunks += 1; } catch { /* Deadline cancelled the source. */ }
    }, 5);
    try {
      await assertProxyFailure(await withinBodyDeadline(h.call(request)), 408, "REQUEST_BODY_TIMEOUT");
      assert.ok(chunks >= 2, "several chunks arrived without an idle gap");
      assert.equal(sourceCancelled, true);
      assert.equal(source.body.locked, false);
      assert.equal(upstreamSignal.aborted, true);
      assert.equal(listeners.size, 0);
    } finally {
      clearInterval(trickle);
    }
  }, { fetch: async (_url, options) => { upstreamSignal = options.signal; return new Response("early"); } });
});

for (const when of ["already aborted", "during drain"]) {
  test(`slow-client disconnect ${when} returns 499 and releases ownership`, async () => {
    let upstreamSignal;
    let cancelled = false;
    await withSlowClientProxy(async (h) => {
      const source = h.openBody(() => { cancelled = true; });
      const abort = new AbortController();
      const request = proxyRequest("POST", source.body);
      // Request.signal is normally supplied by the server; use a real native signal here.
      Object.defineProperty(request, "signal", { value: abort.signal });
      const listeners = observeAbortListeners(request.signal);
      if (when === "already aborted") abort.abort(new Error("client disconnected"));
      const response = h.call(request);
      if (when === "during drain") {
        await bodyPause(5);
        abort.abort(new Error("client disconnected"));
      }
      await assertProxyFailure(await withinBodyDeadline(response), 499, "REQUEST_CLIENT_DISCONNECTED");
      assert.equal(cancelled, true);
      assert.equal(source.body.locked, false);
      if (upstreamSignal) assert.equal(upstreamSignal.aborted, true);
      assert.equal(listeners.size, 0);
    }, { fetch: async (_url, options) => { upstreamSignal = options.signal; return new Response("early"); } });
  });
}

test("slow-client body read failure without a disconnect signal stays distinct from timeout and oversized", async () => {
  await withSlowClientProxy(async (h) => {
    const source = h.openBody();
    const request = proxyRequest("POST", source.body);
    const listeners = observeAbortListeners(request.signal);
    const pending = h.call(request);
    await bodyPause(5);
    // A stream error alone cannot prove a client disconnect: only signal abort maps to 499.
    source.error(new Error("body read failed"));
    await assertProxyFailure(await withinBodyDeadline(pending), 400, "REQUEST_BODY_READ_FAILED");
    assert.equal(source.body.locked, false);
    assert.equal(listeners.size, 0);
  });
});

for (const scenario of ["declared oversized", "observed oversized", "deadline"]) {
  test(`slow-client hung request and upstream cancellation cannot block ${scenario}`, async () => {
    let sourceCancelled = false;
    let upstreamCancelled = false;
    let fetchCount = 0;
    const never = new Promise(() => {});
    await withSlowClientProxy(async (h) => {
      const source = h.openBody(() => { sourceCancelled = true; return never; });
      if (scenario === "observed oversized") source.enqueue(new Uint8Array(1024));
      const request = proxyRequest("POST", source.body, scenario === "declared oversized" ? { "content-length": "1025" } : {});
      const response = await withinBodyDeadline(h.call(request));
      await assertProxyFailure(response, scenario === "deadline" ? 408 : 413,
        scenario === "deadline" ? "REQUEST_BODY_TIMEOUT" : "REQUEST_BODY_TOO_LARGE");
      assert.equal(sourceCancelled, true);
      assert.equal(source.body.locked, false);
      if (scenario === "declared oversized") assert.equal(fetchCount, 0);
      else assert.equal(upstreamCancelled, true);
    }, {
      fetch: async () => {
        fetchCount += 1;
        return new Response(new ReadableStream({ cancel() { upstreamCancelled = true; return never; } }));
      },
    });
  });
}

test("slow-client sixteen concurrent open bodies all time out with bounded cleanup", async () => {
  const signals = [];
  const cancelled = [];
  await withSlowClientProxy(async (h) => {
    const sources = Array.from({ length: 16 }, (_, index) => h.openBody(() => { cancelled[index] = true; }));
    const requests = sources.map((source) => proxyRequest("POST", source.body));
    const listeners = requests.map((request) => observeAbortListeners(request.signal));
    const responses = await withinBodyDeadline(Promise.all(requests.map((request) => h.call(request))), 350);
    for (const response of responses) await assertProxyFailure(response, 408, "REQUEST_BODY_TIMEOUT");
    assert.equal(signals.length, 16);
    assert.ok(signals.every((signal) => signal.aborted));
    assert.deepEqual(cancelled, Array(16).fill(true));
    assert.ok(sources.every((source) => !source.body.locked));
    assert.ok(listeners.every((set) => set.size === 0));
  }, { fetch: async (_url, options) => { signals.push(options.signal); return new Response("early"); } });
});

for (const first of ["oversized", "deadline"]) {
  test(`slow-client first terminal failure wins when ${first} is observed first`, async () => {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody();
      const pending = h.call(proxyRequest("POST", source.body));
      if (first === "oversized") {
        source.enqueue(new Uint8Array(1024));
        await assertProxyFailure(await withinBodyDeadline(pending), 413, "REQUEST_BODY_TOO_LARGE");
        await bodyPause(60);
      } else {
        await assertProxyFailure(await withinBodyDeadline(pending), 408, "REQUEST_BODY_TIMEOUT");
        assert.throws(() => source.enqueue(new Uint8Array(1024)), TypeError);
      }
    });
  });
}

test("slow-client valid early response waits for input EOF then removes body deadline", async () => {
  let upstreamSignal;
  await withSlowClientProxy(async (h) => {
    const source = h.openBody();
    const request = proxyRequest("POST", source.body);
    const listeners = observeAbortListeners(request.signal);
    const pending = h.call(request);
    await bodyPause(5);
    source.enqueue();
    source.close();
    const response = await withinBodyDeadline(pending);
    assert.equal(response.status, 422);
    assert.equal(await response.text(), "invalid");
    assert.equal(h.timers.size, 0);
    assert.equal(source.body.locked, false);
    assert.equal(listeners.size, 0);
    await bodyPause(60);
    assert.equal(upstreamSignal.aborted, false);
  }, { fetch: async (_url, options) => { upstreamSignal = options.signal; return new Response("invalid", { status: 422 }); } });
});

test("slow-client completed body keeps backend connection failure as 503", async () => {
  await withSlowClientProxy(async (h) => {
    const source = h.openBody();
    source.close();
    await assertProxyFailure(await withinBodyDeadline(h.call(proxyRequest("POST", source.body))), 503, "BACKEND_UNAVAILABLE");
    assert.equal(source.body.locked, false);
  }, { fetch: async () => { throw new TypeError("fetch failed"); } });
});

test("slow-client input EOF ends deadline while upstream fetch and response streaming continue", async () => {
  let responseController;
  let upstreamSignal;
  let upstreamCancelled = false;
  const encoder = new TextEncoder();
  const upstreamBody = new ReadableStream({
    start(controller) { responseController = controller; controller.enqueue(encoder.encode("first")); },
    cancel() { upstreamCancelled = true; },
  });
  try {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody();
      source.close();
      const response = await withinBodyDeadline(h.call(proxyRequest("POST", source.body)));
      assert.equal(response.status, 200);
      assert.equal(h.timers.size, 0);
      const reader = response.body.getReader();
      assert.equal(new TextDecoder().decode((await withinBodyDeadline(reader.read())).value), "first");
      await bodyPause(60);
      responseController.enqueue(encoder.encode("second"));
      responseController.close();
      assert.equal(new TextDecoder().decode((await withinBodyDeadline(reader.read())).value), "second");
      assert.equal((await withinBodyDeadline(reader.read())).done, true);
      reader.releaseLock();
      assert.equal(upstreamSignal.aborted, false);
      assert.equal(upstreamCancelled, false);
    }, {
      fetch: async (_url, options) => {
        upstreamSignal = options.signal;
        const reader = options.body.getReader();
        try { while (!(await reader.read()).done) {} } finally { reader.releaseLock(); }
        // Body EOF must clear its timer before fetch itself resolves.
        await bodyPause(60);
        return new Response(upstreamBody);
      },
    });
  } finally {
    try { responseController.close(); } catch { /* Already closed. */ }
  }
});

for (const [configured, expected] of [
  [undefined, 60000], ["", 60000], ["0", 60000], ["-1", 60000],
  ["1.5", 60000], ["invalid", 60000], ["Infinity", 60000],
  ["3600001", 60000], ["1", 1], ["3600000", 3600000],
]) {
  test(`slow-client timeout configuration ${String(configured)} schedules ${expected}ms`, async () => {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody();
      source.close();
      const response = await withinBodyDeadline(h.call(proxyRequest("POST", source.body)));
      assert.equal(response.status, 422);
      assert.ok(h.delays.includes(expected), `configured deadline missing from ${JSON.stringify(h.delays)}`);
      assert.equal(h.timers.size, 0);
    }, { timeout: configured });
  });
}

for (const first of ["disconnect", "deadline"]) {
  test(`slow-client ${first} remains authoritative when the other terminal event follows`, async () => {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody();
      const abort = new AbortController();
      const request = proxyRequest("POST", source.body);
      Object.defineProperty(request, "signal", { value: abort.signal });
      const listeners = observeAbortListeners(request.signal);
      const pending = h.call(request);
      if (first === "disconnect") {
        await bodyPause(5);
        abort.abort(new Error("client disconnected"));
        await assertProxyFailure(await withinBodyDeadline(pending), 499, "REQUEST_CLIENT_DISCONNECTED");
        await bodyPause(60);
      } else {
        await assertProxyFailure(await withinBodyDeadline(pending), 408, "REQUEST_BODY_TIMEOUT");
        abort.abort(new Error("late client disconnect"));
      }
      assert.equal(listeners.size, 0);
      assert.equal(source.body.locked, false);
    });
  });
}

test("slow-client hung fetch ignoring abort cannot block timeout and its late body is cancelled", async () => {
  let resolveFetch;
  let upstreamSignal;
  let lateBodyCancelled = false;
  const fetching = new Promise((resolve) => { resolveFetch = resolve; });
  try {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody();
      const response = await withinBodyDeadline(h.call(proxyRequest("POST", source.body)));
      await assertProxyFailure(response, 408, "REQUEST_BODY_TIMEOUT");
      assert.equal(upstreamSignal.aborted, true);
      assert.equal(source.body.locked, false);
      resolveFetch(new Response(new ReadableStream({
        cancel() { lateBodyCancelled = true; return new Promise(() => {}); },
      })));
      await Promise.resolve();
      await Promise.resolve();
      assert.equal(lateBodyCancelled, true);
    }, { fetch: (_url, options) => { upstreamSignal = options.signal; return fetching; } });
  } finally {
    resolveFetch(new Response("cleanup"));
  }
});

test("slow-client rejected cancellation hooks are handled without unhandled rejections", async () => {
  const unhandled = [];
  const onUnhandled = (error) => unhandled.push(error);
  process.on("unhandledRejection", onUnhandled);
  try {
    await withSlowClientProxy(async (h) => {
      const source = h.openBody(() => Promise.reject(new Error("source cancel failed")));
      await assertProxyFailure(await withinBodyDeadline(h.call(proxyRequest("POST", source.body))), 408, "REQUEST_BODY_TIMEOUT");
      assert.equal(source.body.locked, false);
      // Give rejected cancellation hooks an event-loop turn to surface.
      await new Promise((resolve) => setImmediate(resolve));
      assert.deepEqual(unhandled, []);
    }, { fetch: async () => new Response(new ReadableStream({
      cancel() { return Promise.reject(new Error("upstream cancel failed")); },
    })) });
  } finally {
    process.removeListener("unhandledRejection", onUnhandled);
  }
});
