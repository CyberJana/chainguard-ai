import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/server-api";

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected" }, { status: 403 });
  }
  const cookieStore = await cookies();
  const token = cookieStore.get("chainguard_session")?.value;
  if (!token) return new NextResponse(null, { status: 204 });
  try {
    const upstream = await backendFetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!upstream.ok && upstream.status !== 401) {
      return NextResponse.json({ detail: "Could not revoke session" }, { status: upstream.status });
    }
    const response = new NextResponse(null, { status: 204 });
    response.cookies.delete("chainguard_session");
    return response;
  } catch (error) {
    if (error instanceof TypeError || error instanceof DOMException) {
      return NextResponse.json({ detail: "Authentication service is unavailable" }, { status: 502 });
    }
    throw error;
  }
}
