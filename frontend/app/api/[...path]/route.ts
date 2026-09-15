import { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const DEFAULT_BACKEND_URL = "http://localhost:8000";
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

async function proxy(request: NextRequest, context: RouteContext): Promise<Response> {
  const { path } = await context.params;
  const method = request.method.toUpperCase();
  const hasRequestBody = !["GET", "HEAD"].includes(method);
  const body = hasRequestBody ? await request.arrayBuffer() : undefined;
  const target = buildBackendUrl(request, path);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
    method,
    headers: buildForwardedHeaders(request),
    body,
    cache: "no-store",
    redirect: "manual",
    });
  } catch (error) {
    if (!(error instanceof TypeError)) throw error;
    return Response.json(
      { error: { code: "BACKEND_UNAVAILABLE", message: "Backend is unavailable. Please try again shortly.", trace_id: crypto.randomUUID(), details: {} } },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    );
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: buildResponseHeaders(upstream.headers),
  });
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
