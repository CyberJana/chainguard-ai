export function backendUrl(path: string): URL {
  const configured = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
  const base = new URL(configured);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password || base.search || base.hash) {
    throw new Error("BACKEND_API_URL must be an HTTP(S) URL without credentials, query, or fragment");
  }
  return new URL(path, base);
}

export function backendFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(backendUrl(path), {
    ...init,
    cache: "no-store",
    signal: init.signal ?? AbortSignal.timeout(10_000),
  });
}
