import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { backendFetch } from "@/lib/server-api";

export async function GET() {
  const token = (await cookies()).get("chainguard_session")?.value;
  if (!token) return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  try {
    const response = await backendFetch("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const body = await response.json();
    if (!response.ok) {
      const result = NextResponse.json(body, { status: response.status });
      if (response.status === 401) result.cookies.delete("chainguard_session");
      return result;
    }
    return NextResponse.json(body, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (error instanceof TypeError || error instanceof DOMException) {
      return NextResponse.json({ detail: "Authentication service is unavailable" }, { status: 502 });
    }
    throw error;
  }
}
