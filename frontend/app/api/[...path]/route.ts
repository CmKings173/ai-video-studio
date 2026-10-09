import { NextRequest } from "next/server";
import { forwardClientIdentity } from "@/lib/server/client-identity";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DEFAULT_BACKEND_URL = "http://localhost:8000";
const DEFAULT_REQUEST_BODY_MAX_BYTES = 2 * 1024 * 1024;
const DEFAULT_REQUEST_BODY_TIMEOUT_MS = 60_000;
const FORWARDED_REQUEST_HEADERS = [
  "accept",
  "content-type",
  "cookie",
  "if-match",
  "idempotency-key",
  "x-csrf-token",
] as const;
const BLOCKED_RESPONSE_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "transfer-encoding",
  "upgrade",
]);

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

function backendBaseUrl(): string {
  return (process.env.API_BACKEND_URL || DEFAULT_BACKEND_URL).replace(/\/+$/, "");
}

function requestBodyMaxBytes(): number {
  const configured = process.env.REQUEST_BODY_MAX_BYTES;
  if (configured === undefined || configured === "") return DEFAULT_REQUEST_BODY_MAX_BYTES;
  const parsed = Number(configured);
  return Number.isSafeInteger(parsed) && parsed >= 1024
    ? parsed
    : DEFAULT_REQUEST_BODY_MAX_BYTES;
}

function requestBodyTimeoutMs(): number {
  const value = Number(process.env.PROXY_REQUEST_BODY_TIMEOUT_MS);
  return Number.isSafeInteger(value) && value > 0 && value <= 3_600_000
    ? value
    : DEFAULT_REQUEST_BODY_TIMEOUT_MS;
}

function buildBackendUrl(request: NextRequest, path: string[]): string {
  const target = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, backendBaseUrl());
  target.search = request.nextUrl.search;
  return target.toString();
}

function buildForwardedHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) {
      headers.set(name, value);
    }
  }
  headers.set("x-forwarded-host", request.nextUrl.host);
  headers.set("x-forwarded-proto", request.nextUrl.protocol.replace(":", ""));
  forwardClientIdentity(request.headers, headers);
  return headers;
}

function buildResponseHeaders(upstreamHeaders: Headers): Headers {
  const headers = new Headers();
  upstreamHeaders.forEach((value, key) => {
    if (!BLOCKED_RESPONSE_HEADERS.has(key.toLowerCase())) {
      headers.append(key, value);
    }
  });
  return headers;
}

function declaredBodyExceedsLimit(request: NextRequest, maxBytes: number): boolean {
  const contentLength = request.headers.get("content-length");
  return contentLength !== null && /^\d+$/.test(contentLength) && Number(contentLength) > maxBytes;
}

function cancelBody(body: ReadableStream<Uint8Array> | null, reason?: unknown): void {
  if (!body || body.locked) return;
  try {
    void body.cancel(reason).catch(() => {});
  } catch {
    // A disconnected or already-cancelled stream needs no further cleanup.
  }
}

type LimitedRequestBody = {
  stream: ReadableStream<Uint8Array>;
  drain(): Promise<void>;
  stop(reason: Error): void;
};

class RequestBodyError extends Error {
  constructor(public readonly code: string, message: string, public readonly status: number) {
    super(message);
  }
}

function limitedRequestBody(
  request: NextRequest,
  maxBytes: number,
  onFailure: (reason: Error) => void,
): LimitedRequestBody | undefined {
  if (!request.body) return undefined;

  const reader = request.body.getReader();
  let receivedBytes = 0;
  let complete = false;
  let draining = false;
  let failure: Error | undefined;
  let readQueue: Promise<void> = Promise.resolve();
  let interruptRead: ((reason: Error) => void) | undefined;
  const timer = setTimeout(() => stop(new RequestBodyError(
    "REQUEST_BODY_TIMEOUT", "Request body did not finish within the configured deadline", 408,
  )), requestBodyTimeoutMs());

  function stop(reason: Error): void {
    if (complete || failure) return;
    failure = reason;
    clearTimeout(timer);
    interruptRead?.(reason);
    // Stream cancellation hooks can return a never-settling promise. Initiate
    // cancellation but release ownership without awaiting that promise.
    void reader.cancel(reason).catch(() => {});
    reader.releaseLock();
    onFailure(reason);
  }

  function readNext(): Promise<ReadableStreamReadResult<Uint8Array>> {
    const next = readQueue.then(async () => {
      if (failure) throw failure;
      if (complete) {
        return { done: true, value: undefined } as ReadableStreamReadResult<Uint8Array>;
      }

      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        // Only one serialized read owns an interruption callback. Racing every
        // chunk against one pending promise would retain callbacks until EOF.
        result = await new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
          interruptRead = reject;
          void reader.read().then(resolve, reject);
        });
      } catch (error) {
        if (!failure) stop(new RequestBodyError(
          "REQUEST_BODY_READ_FAILED", "Request body could not be read", 400,
        ));
        throw failure ?? error;
      } finally {
        interruptRead = undefined;
      }
      if (failure) throw failure;
      if (result.done) {
        complete = true;
        clearTimeout(timer);
        reader.releaseLock();
        return result;
      }

      receivedBytes += result.value.byteLength;
      if (receivedBytes > maxBytes) {
        const error = new RequestBodyError(
          "REQUEST_BODY_TOO_LARGE", "Request body exceeds the configured limit", 413,
        );
        stop(error);
        throw error;
      }

      return result;
    });
    readQueue = next.then(() => undefined, () => undefined);
    return next;
  }

  async function drain(): Promise<void> {
    draining = true;
    while (!complete) {
      await readNext();
    }
  }

  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const result = await readNext();
        if (result.done) {
          controller.close();
        } else if (!draining) {
          controller.enqueue(result.value);
        }
      } catch (error) {
        controller.error(error);
      }
    },
    // An upstream may reject before consuming the body. Check the remainder
    // for actual oversized bytes, still subject to the same absolute deadline.
    cancel: () => drain().catch(() => {}),
  }, { highWaterMark: 0 });

  return { stream, drain, stop };
}

function bodyTooLargeResponse(): Response {
  const traceId = crypto.randomUUID();
  return Response.json(
    {
      error: {
        code: "REQUEST_BODY_TOO_LARGE",
        message: "Request body exceeds the configured limit",
        trace_id: traceId,
        details: {},
      },
    },
    {
      status: 413,
      headers: {
        "Cache-Control": "no-store",
        "X-Request-ID": traceId,
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const method = request.method.toUpperCase();
  const hasRequestBody = !["GET", "HEAD"].includes(method);
  const maxBytes = requestBodyMaxBytes();
  if (hasRequestBody && declaredBodyExceedsLimit(request, maxBytes)) {
    cancelBody(request.body, new Error("Request body exceeds the configured limit"));
    return bodyTooLargeResponse();
  }

  const target = buildBackendUrl(request, path);
  const abortController = new AbortController();
  let failure: Error | undefined;
  let rejectFailure!: (reason: Error) => void;
  const failed = new Promise<never>((_resolve, reject) => { rejectFailure = reject; });
  void failed.catch(() => {});
  const fail = (reason: Error) => {
    if (failure) return;
    failure = reason;
    rejectFailure(reason);
    abortController.abort(reason);
  };
  const limitedBody = hasRequestBody
    ? limitedRequestBody(request, maxBytes, fail)
    : undefined;
  const body = limitedBody?.stream;
  const abortOnClientDisconnect = () => {
    const error = new RequestBodyError(
      "REQUEST_CLIENT_DISCONNECTED", "Client disconnected before the request completed", 499,
    );
    limitedBody?.stop(error);
    fail(error);
  };
  if (request.signal.aborted) {
    abortOnClientDisconnect();
  } else {
    request.signal.addEventListener("abort", abortOnClientDisconnect, { once: true });
  }
  const cleanupRequestSignal = () => request.signal.removeEventListener("abort", abortOnClientDisconnect);

  let upstream: Response | undefined;
  try {
    const fetchOptions: RequestInit & { duplex?: "half" } = {
      method,
      headers: buildForwardedHeaders(request),
      body,
      cache: "no-store",
      redirect: "manual",
      signal: abortController.signal,
    };
    if (body) fetchOptions.duplex = "half";
    if (failure) throw failure;
    const fetching = fetch(target, fetchOptions);
    // Even a late response from a fetch which ignored abort must be disposed.
    void fetching.then((response) => {
      if (failure) cancelBody(response.body, failure);
    }, () => {});
    upstream = await Promise.race([fetching, failed]);
    await limitedBody?.drain();
    if (failure) throw failure;
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: buildResponseHeaders(upstream.headers),
    });
  } catch (error) {
    // Network failure is not evidence of excessive bytes. Check the unfinished
    // input only until its existing deadline; its first terminal failure wins.
    try { await limitedBody?.drain(); } catch { /* failure already recorded */ }
    cancelBody(upstream?.body ?? null, failure ?? error);
    if (failure instanceof RequestBodyError) {
      if (failure.status === 413) return bodyTooLargeResponse();
      const traceId = crypto.randomUUID();
      return Response.json(
        { error: { code: failure.code, message: failure.message, trace_id: traceId, details: {} } },
        { status: failure.status, headers: { "Cache-Control": "no-store", "X-Request-ID": traceId } },
      );
    }
    if (!(error instanceof TypeError)) throw error;
    return Response.json(
      { error: { code: "BACKEND_UNAVAILABLE", message: "Backend is unavailable. Please try again shortly.", trace_id: crypto.randomUUID(), details: {} } },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  } finally {
    cleanupRequestSignal();
  }
}

export async function GET(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}

export async function HEAD(request: NextRequest, context: RouteContext): Promise<Response> {
  return proxy(request, context);
}
