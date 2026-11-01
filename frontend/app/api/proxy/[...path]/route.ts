import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

type RouteContext = { params: Promise<{ path: string[] }> };

function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  return origin !== null && origin === new URL(request.url).origin;
}

function upstreamBase(): URL {
  const base = new URL(process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000");
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) {
    throw new Error("BACKEND_API_URL must be an HTTP(S) URL without embedded credentials");
  }
  return base;
}

async function proxy(request: NextRequest, context: RouteContext): Promise<NextResponse> {
  const method = request.method;
  if (method !== "GET" && !isSameOrigin(request)) {
    return NextResponse.json({ detail: "Cross-origin request rejected" }, { status: 403 });
  }
  const { path } = await context.params;
  if (!path.length || path.some((part) => !/^[a-zA-Z0-9._-]+$/.test(part) || part === "." || part === "..")) {
    return NextResponse.json({ detail: "Invalid API path" }, { status: 400 });
  }
  if (!["transactions", "risk", "alerts", "cases"].includes(path[0])) {
    return NextResponse.json({ detail: "API path is not available through this proxy" }, { status: 404 });
  }
  const token = (await cookies()).get("chainguard_session")?.value;
  if (!token) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });

  try {
    const target = new URL(`/api/v1/${path.map(encodeURIComponent).join("/")}`, upstreamBase());
    target.search = new URL(request.url).search;
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    const contentType = request.headers.get("content-type");
    if (contentType) headers["Content-Type"] = contentType;
    const upstream = await fetch(target, {
      method,
      headers,
      ...(method === "GET" ? {} : { body: await request.arrayBuffer() }),
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
    const responseHeaders = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    for (const header of ["content-type", "content-disposition"]) {
      const value = upstream.headers.get(header);
      if (value) responseHeaders.set(header, value);
    }
    const response = new NextResponse(await upstream.arrayBuffer(), {
      status: upstream.status,
      headers: responseHeaders,
    });
    if (upstream.status === 401) response.cookies.delete("chainguard_session");
    return response;
  } catch (error) {
    if (error instanceof TypeError || error instanceof DOMException) {
      return NextResponse.json({ detail: "Backend API is unavailable" }, { status: 502 });
    }
    throw error;
  }
}

export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
