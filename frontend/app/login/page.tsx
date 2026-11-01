"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck } from "lucide-react";
import type { FormEvent } from "react";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const result = await response.json() as { detail?: string };
      if (!response.ok) throw new Error(result.detail ?? "Sign in failed");
      router.replace("/dashboard");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Sign in failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-5 py-12">
      <section className="panel w-full max-w-md p-8 shadow-2xl shadow-black/20">
        <div className="mb-8 flex items-center gap-3">
          <div className="rounded-xl border border-sky-400/30 bg-sky-400/10 p-3 text-sky-300"><ShieldCheck size={24} /></div>
          <div>
            <p className="text-sm font-bold tracking-[0.18em] text-sky-200">CHAINGUARD AI</p>
            <p className="mt-1 text-xs text-slate-400">Investigation console</p>
          </div>
        </div>
        <h1 className="text-2xl font-semibold">Sign in</h1>
        <p className="mt-2 text-sm text-slate-400">Use your organization account to access cases and alerts.</p>
        <form className="mt-7 space-y-4" onSubmit={submit}>
          <label className="block space-y-2 text-sm">
            <span>Email</span>
            <input className="field" type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </label>
          <label className="block space-y-2 text-sm">
            <span>Password</span>
            <input className="field" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error && <p role="alert" className="rounded-lg border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-200">{error}</p>}
          <button className="button w-full" disabled={pending}>{pending ? "Signing in..." : "Sign in"}</button>
        </form>
        <p className="mt-6 border-t border-slate-700 pt-5 text-xs leading-5 text-slate-500">
          Blockchain risk indicators are triage signals only. The current rules are uncalibrated and are not proof of fraud.
        </p>
      </section>
    </main>
  );
}
