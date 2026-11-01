import { NextRequest, NextResponse } from "next/server";
import { backendFetch } from "@/lib/server-api";

export async function POST(request: NextRequest) {
  const origin = request.headers.get("origin");
  if (!origin || origin !== new URL(request.url).origin) {
    return NextResponse.json({ detail: "Cross-origin request rejected" }, { status: 403 });
  }
  let credentials: unknown;
  try {
    credentials = await request.json();
  } catch {
    return NextResponse.json({ detail: "Invalid JSON request" }, { status: 400 });
  }

  try {
    const login = await backendFetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(credentials),
    });
    const result = await login.json() as { access_token?: string; expires_in?: number; detail?: string };
    if (!login.ok) return NextResponse.json({ detail: result.detail ?? "Sign in failed" }, { status: login.status });
    if (!result.access_token || !result.expires_in) {
      return NextResponse.json({ detail: "Authentication service returned an invalid session" }, { status: 502 });
    }
    const profile = await backendFetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${result.access_token}` },
    });
    if (!profile.ok) return NextResponse.json({ detail: "Could not load user profile" }, { status: 502 });
    const response = NextResponse.json(await profile.json());
    response.cookies.set("chainguard_session", result.access_token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
      maxAge: result.expires_in,
    });
    return response;
  } catch (error) {
    if (error instanceof TypeError || error instanceof DOMException) {
      return NextResponse.json({ detail: "Authentication service is unavailable" }, { status: 502 });
    }
    throw error;
  }
}
