"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import {
  Activity, AlertTriangle, ArrowUpRight, Blocks, Check, ChevronRight, CircleHelp,
  FileSearch, FolderKanban, LogOut, Plus, RefreshCw, Search, ShieldCheck,
} from "lucide-react";
import {
  Cell, Pie, PieChart, ResponsiveContainer, Tooltip,
} from "recharts";
import { Alert, ApiError, api, BitcoinAddressHistory, CaseRecord, errorMessage, RiskAssessment, Transaction } from "@/lib/api";
import type { FormEvent, ReactNode } from "react";

const PIE_COLORS: Record<Alert["severity"], string> = {
  medium: "#f5b942",
  high: "#fb8332",
  critical: "#ff5d70",
};

function Card({ children, className = "" }: Readonly<{ children: ReactNode; className?: string }>) {
  return <section className={`panel p-5 ${className}`}>{children}</section>;
}

function StatusPill({ children, tone = "blue" }: Readonly<{ children: ReactNode; tone?: "blue" | "green" | "amber" | "red" }>) {
  const styles = {
    blue: "border-sky-400/25 bg-sky-400/10 text-sky-200",
    green: "border-teal-400/25 bg-teal-400/10 text-teal-200",
    amber: "border-amber-400/25 bg-amber-400/10 text-amber-200",
    red: "border-rose-400/25 bg-rose-400/10 text-rose-200",
  };
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${styles[tone]}`}>{children}</span>;
}

function severityTone(severity: Alert["severity"]): "amber" | "red" {
  return severity === "medium" ? "amber" : "red";
}

export default function DashboardPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [txid, setTxid] = useState("");
  const [transaction, setTransaction] = useState<Transaction | null>(null);
  const [address, setAddress] = useState("");
  const [addressHistory, setAddressHistory] = useState<BitcoinAddressHistory | null>(null);
  const [risk, setRisk] = useState<RiskAssessment | null>(null);
  const [caseTitle, setCaseTitle] = useState("");
  const [notice, setNotice] = useState("");
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const alertsQuery = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, enabled: me.isSuccess });
  const casesQuery = useQuery({ queryKey: ["cases"], queryFn: api.cases, enabled: me.isSuccess });
  const transactionsQuery = useQuery({ queryKey: ["transactions"], queryFn: api.transactions, enabled: me.isSuccess });

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) router.replace("/login");
  }, [me.error, router]);

  const lookup = useMutation({
    mutationFn: api.lookupTransaction,
    onSuccess: (result) => {
      setTransaction(result);
      setRisk(null);
      setNotice("");
      void queryClient.invalidateQueries({ queryKey: ["transactions"] });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const lookupAddress = useMutation({
    mutationFn: ({ address: requestedAddress, cursor }: { address: string; cursor?: string }) =>
      api.lookupBitcoinAddress(requestedAddress, cursor),
    onSuccess: (result, variables) => {
      setAddressHistory((previous) =>
        variables.cursor && previous?.address === result.address
          ? { ...result, transactions: [...previous.transactions, ...result.transactions] }
          : result,
      );
      setNotice("");
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const analyze = useMutation({
    mutationFn: api.analyze,
    onSuccess: (result) => {
      setRisk(result);
      setNotice("");
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const addCase = useMutation({
    mutationFn: api.createCase,
    onSuccess: () => {
      setCaseTitle("");
      setNotice("Case created.");
      void queryClient.invalidateQueries({ queryKey: ["cases"] });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const updateAlert = useMutation({
    mutationFn: ({ id, status }: { id: number; status: Alert["status"] }) => api.updateAlert(id, status),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["alerts"] });
      setNotice("Alert updated.");
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const severityData = useMemo(() => {
    const counts = (alertsQuery.data ?? []).reduce<Record<string, number>>((accumulator, alert) => {
      accumulator[alert.severity] = (accumulator[alert.severity] ?? 0) + 1;
      return accumulator;
    }, {});
    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [alertsQuery.data]);

  async function submitLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("");
    setTransaction(null);
    setRisk(null);
    lookup.mutate(txid.trim());
  }

  async function submitCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (caseTitle.trim()) addCase.mutate(caseTitle.trim());
  }

  async function submitAddressLookup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setNotice("");
    setAddressHistory(null);
    lookupAddress.mutate({ address: address.trim() });
  }

  async function signOut() {
    try {
      await api.logout();
      router.replace("/login");
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  const alerts = alertsQuery.data ?? [];
  const cases = casesQuery.data ?? [];
  const transactions = transactionsQuery.data ?? [];
  const loading = me.isPending || (me.isSuccess && (alertsQuery.isPending || casesQuery.isPending || transactionsQuery.isPending));
  const dashboardError = [me.error, alertsQuery.error, casesQuery.error, transactionsQuery.error].find(Boolean);

  return (
    <main className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-800/90 bg-[#090f17]/90 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1500px] items-center justify-between gap-5 px-5 py-4 lg:px-8">
          <a href="#overview" className="flex items-center gap-3">
            <div className="rounded-lg border border-sky-400/30 bg-sky-400/10 p-2 text-sky-300"><ShieldCheck size={21} /></div>
            <div><p className="text-sm font-bold tracking-[0.17em]">CHAINGUARD <span className="text-sky-300">AI</span></p><p className="mt-0.5 text-[11px] text-slate-500">INVESTIGATION CONSOLE</p></div>
          </a>
          <nav aria-label="Primary navigation" className="hidden items-center gap-1 md:flex">
            <a className="rounded-lg bg-sky-400/10 px-3 py-2 text-sm text-sky-200" href="#overview">Overview</a>
            <a className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:bg-white/5 hover:text-white" href="#transactions">Transactions</a>
            <a className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:bg-white/5 hover:text-white" href="#addresses">Addresses</a>
            <a className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:bg-white/5 hover:text-white" href="#alerts">Alerts</a>
            <a className="rounded-lg px-3 py-2 text-sm text-slate-400 hover:bg-white/5 hover:text-white" href="#cases">Cases</a>
          </nav>
          <div className="flex items-center gap-3">
            {me.data && <span className="hidden text-right sm:block"><span className="block text-xs text-slate-200">{me.data.email}</span><span className="mt-0.5 block text-[10px] uppercase tracking-wider text-slate-500">{me.data.role}</span></span>}
            <button type="button" className="button secondary !px-3 !py-2" onClick={signOut} aria-label="Sign out"><LogOut size={16} /><span className="hidden sm:inline">Sign out</span></button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] space-y-6 px-5 py-7 lg:px-8">
        <section id="overview" className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <div className="mb-3 flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-teal-400 shadow-[0_0_14px_#35d0bd]" /><span className="label">Analyst workspace</span></div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Security overview</h1>
            <p className="mt-2 text-sm text-slate-400">Public-chain observations and organization-scoped investigative work.</p>
          </div>
          <StatusPill tone="amber"><CircleHelp size={13} /> Rules are uncalibrated</StatusPill>
        </section>

        {loading && <Card><p className="flex items-center gap-2 text-sm text-slate-400"><RefreshCw size={15} className="animate-spin" /> Loading workspace data...</p></Card>}
        {dashboardError && !loading && <Card><p role="alert" className="text-sm text-rose-200">Could not load the dashboard: {errorMessage(dashboardError)}</p><p className="mt-2 text-xs text-slate-500">Confirm the API is running, then refresh.</p></Card>}
        {notice && <p role="status" className="rounded-lg border border-sky-400/20 bg-sky-400/10 px-4 py-3 text-sm text-sky-100">{notice}</p>}

        {!loading && !dashboardError && (
          <>
            <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard label="Open alerts" value={alerts.filter((alert) => alert.status === "open").length} note="Current organization" icon={<AlertTriangle size={17} />} tone="amber" />
              <StatCard label="Active cases" value={cases.filter((item) => item.status !== "closed").length} note="Open and in review" icon={<FolderKanban size={17} />} tone="blue" />
              <StatCard label="Stored transactions" value={transactions.length} note="Most recent 20" icon={<Blocks size={17} />} tone="teal" />
              <StatCard label="Critical alerts" value={alerts.filter((alert) => alert.severity === "critical").length} note="Analyst review required" icon={<Activity size={17} />} tone="violet" />
            </section>

            <section className="grid gap-5 xl:grid-cols-[1.4fr_0.8fr]">
              <Card>
                <div className="mb-5 flex items-center justify-between">
                  <div><p className="label">Bitcoin explorer</p><h2 className="mt-1 text-lg font-semibold">Investigate a transaction</h2></div>
                  <FileSearch className="text-slate-500" size={19} />
                </div>
                <form onSubmit={submitLookup} className="flex flex-col gap-3 sm:flex-row">
                  <label className="sr-only" htmlFor="txid">Bitcoin transaction ID</label>
                  <input id="txid" className="field font-mono text-sm" placeholder="64-character transaction ID" minLength={64} maxLength={64} pattern="[a-fA-F0-9]{64}" required value={txid} onChange={(event) => setTxid(event.target.value)} />
                  <button className="button shrink-0" disabled={lookup.isPending}><Search size={16} />{lookup.isPending ? "Looking up..." : "Lookup"}</button>
                </form>
                {transaction && (
                  <div className="mt-5 rounded-xl border border-slate-700/80 bg-[#0b141e] p-4">
                    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
                      <div className="min-w-0"><p className="label">Transaction</p><p className="mt-1 break-all font-mono text-xs text-sky-200">{transaction.txid}</p></div>
                      <StatusPill tone={transaction.is_confirmed ? "green" : "amber"}>{transaction.is_confirmed === null ? "Confirmation unknown" : transaction.is_confirmed ? "Confirmed" : "Unconfirmed"}</StatusPill>
                    </div>
                    <div className="mt-4 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
                      <DataCell label="Network" value={transaction.network} />
                      <DataCell label="Block height" value={transaction.block_height?.toLocaleString() ?? "Pending"} />
                      <DataCell label="Inputs / outputs" value={`${transaction.input_count} / ${transaction.output_count}`} />
                      <DataCell label="Provider" value={transaction.source} />
                    </div>
                    <button className="button secondary mt-4" onClick={() => analyze.mutate(transaction.txid)} disabled={analyze.isPending}>
                      <Activity size={15} />{analyze.isPending ? "Analyzing..." : "Analyze risk indicators"}
                    </button>
                  </div>
                )}
                {risk && (
                  <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/5 p-4" role="status">
                    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-semibold">Risk triage: {risk.score}/100</h3><StatusPill tone={risk.category === "low" ? "green" : "amber"}>{risk.category}</StatusPill></div>
                    <p className="mt-1 text-xs text-amber-100/70">{risk.model_version} · Not calibrated · Not a probability of fraud</p>
                    {risk.explanation.length > 0 ? <ul className="mt-3 space-y-2 text-sm text-slate-300">{risk.explanation.map((indicator) => <li key={indicator.indicator} className="flex items-start gap-2"><ArrowUpRight size={14} className="mt-0.5 shrink-0 text-amber-300" /><span>{indicator.indicator.replaceAll("_", " ")} <span className="text-slate-500">(+{indicator.weight})</span></span></li>)}</ul> : <p className="mt-3 text-sm text-slate-400">No configured heuristic indicator was triggered.</p>}
                    <p className="mt-3 text-xs leading-5 text-slate-400">{risk.recommendation}</p>
                  </div>
                )}
              </Card>

              <Card>
                <div className="mb-3"><p className="label">Alert distribution</p><h2 className="mt-1 text-lg font-semibold">Severity overview</h2></div>
                {severityData.length ? (
                  <div className="h-52">
                    <ResponsiveContainer width="100%" height="100%">
                      <PieChart>
                        <Pie data={severityData} dataKey="value" nameKey="name" innerRadius={56} outerRadius={78} paddingAngle={4} stroke="none">
                          {severityData.map((item) => <Cell key={item.name} fill={PIE_COLORS[item.name as Alert["severity"]]} />)}
                        </Pie>
                        <Tooltip contentStyle={{ background: "#111b27", border: "1px solid #203143", borderRadius: 8 }} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>
                ) : <div className="flex h-52 items-center justify-center text-sm text-slate-500">No alert data available.</div>}
                <div className="flex flex-wrap justify-center gap-4 text-xs text-slate-400">
                  {(["medium", "high", "critical"] as const).map((severity) => <span key={severity} className="flex items-center gap-2"><i className="h-2 w-2 rounded-full" style={{ backgroundColor: PIE_COLORS[severity] }} />{severity}</span>)}
                </div>
              </Card>
            </section>

            <section id="addresses">
              <Card>
                <div className="mb-5 flex items-center justify-between">
                  <div><p className="label">Wallet explorer</p><h2 className="mt-1 text-lg font-semibold">Bitcoin address history</h2></div>
                  <Search className="text-slate-500" size={19} />
                </div>
                <form onSubmit={submitAddressLookup} className="flex flex-col gap-3 sm:flex-row">
                  <label className="sr-only" htmlFor="bitcoin-address">Bitcoin address</label>
                  <input id="bitcoin-address" className="field font-mono text-sm" placeholder="Bitcoin mainnet address" minLength={26} maxLength={74} pattern="(?:[13][1-9A-HJ-NP-Za-km-z]{25,34}|bc1[ac-hj-np-z02-9]{11,71})" required value={address} onChange={(event) => setAddress(event.target.value)} />
                  <button className="button shrink-0" disabled={lookupAddress.isPending}><Search size={16} />{lookupAddress.isPending ? "Searching..." : "Search address"}</button>
                </form>
                {addressHistory && (
                  <div className="mt-5">
                    <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                      <p className="break-all font-mono text-xs text-sky-200">{addressHistory.address}</p>
                      <p className="text-xs text-slate-500">Source: {addressHistory.source} · Retrieved {new Date(addressHistory.retrieved_at).toLocaleString()}</p>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                      <DataCell label="Confirmed transactions" value={addressHistory.chain_stats.tx_count.toLocaleString()} />
                      <DataCell label="Confirmed received" value={`${(addressHistory.chain_stats.funded_txo_sum / 100_000_000).toFixed(8)} BTC`} />
                      <DataCell label="Confirmed spent" value={`${(addressHistory.chain_stats.spent_txo_sum / 100_000_000).toFixed(8)} BTC`} />
                      <DataCell label="Mempool transactions" value={addressHistory.mempool_stats.tx_count.toLocaleString()} />
                    </div>
                    <div className="mt-5 overflow-x-auto">
                      <table className="w-full min-w-[640px] text-left text-sm">
                        <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500"><tr><th className="py-3 pr-4 font-medium">Transaction</th><th className="py-3 pr-4 font-medium">Status</th><th className="py-3 pr-4 font-medium">Block</th><th className="py-3 pr-4 font-medium">Outputs</th><th className="py-3 text-right font-medium">Action</th></tr></thead>
                        <tbody className="divide-y divide-slate-800/70">
                          {addressHistory.transactions.map((item) => <tr key={item.txid}>
                            <td className="py-3 pr-4 font-mono text-xs text-slate-300">{item.txid.slice(0, 18)}…</td>
                            <td className="py-3 pr-4">{item.is_confirmed === null ? <StatusPill tone="blue">Unknown</StatusPill> : item.is_confirmed ? <StatusPill tone="green">Confirmed</StatusPill> : <StatusPill tone="amber">Unconfirmed</StatusPill>}</td>
                            <td className="py-3 pr-4 text-slate-400">{item.block_height?.toLocaleString() ?? "Pending"}</td>
                            <td className="py-3 pr-4 text-slate-400">{item.output_count}</td>
                            <td className="py-3 text-right"><button type="button" className="text-xs text-sky-300 hover:text-sky-200" onClick={() => { setTxid(item.txid); lookup.mutate(item.txid); }}>Inspect</button></td>
                          </tr>)}
                        </tbody>
                      </table>
                    </div>
                    {addressHistory.transactions.length === 0 && <p className="py-4 text-center text-sm text-slate-500">No recent transactions returned by the provider.</p>}
                    {addressHistory.next_cursor && <button className="button secondary mt-4" disabled={lookupAddress.isPending} onClick={() => lookupAddress.mutate({ address: addressHistory.address, cursor: addressHistory.next_cursor ?? undefined })}>{lookupAddress.isPending ? "Loading..." : "Load older transactions"}</button>}
                    <p className="mt-3 text-xs text-slate-500">Address history is fetched live from the configured provider. Mempool activity is shown separately from confirmed chain totals.</p>
                  </div>
                )}
              </Card>
            </section>

            <section id="alerts">
              <Card>
                <div className="mb-4 flex items-center justify-between"><div><p className="label">Triage queue</p><h2 className="mt-1 text-lg font-semibold">Recent alerts</h2></div><AlertTriangle className="text-amber-300" size={18} /></div>
                {alerts.length === 0 ? <EmptyState text="No alerts in this organization yet." /> : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[700px] text-left text-sm">
                      <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500"><tr><th className="py-3 pr-4 font-medium">Alert</th><th className="py-3 pr-4 font-medium">Severity</th><th className="py-3 pr-4 font-medium">Status</th><th className="py-3 pr-4 font-medium">Created</th><th className="py-3 text-right font-medium">Action</th></tr></thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {alerts.map((alert) => <tr key={alert.id}>
                          <td className="py-3 pr-4 text-slate-300">Alert #{alert.id}</td>
                          <td className="py-3 pr-4"><StatusPill tone={severityTone(alert.severity)}>{alert.severity}</StatusPill></td>
                          <td className="py-3 pr-4 text-slate-400">{alert.status}</td>
                          <td className="py-3 pr-4 text-slate-500">{new Date(alert.created_at).toLocaleString()}</td>
                          <td className="py-3 text-right">
                            {alert.status === "open" && <button className="button secondary !px-2.5 !py-1.5 text-xs" onClick={() => updateAlert.mutate({ id: alert.id, status: "acknowledged" })} disabled={updateAlert.isPending}><Check size={13} />Acknowledge</button>}
                          </td>
                        </tr>)}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </section>

            <section id="cases" className="grid gap-5 xl:grid-cols-[0.75fr_1.25fr]">
              <Card>
                <p className="label">Investigation workflow</p><h2 className="mt-1 text-lg font-semibold">Create a case</h2>
                <form onSubmit={submitCase} className="mt-4 space-y-3">
                  <label htmlFor="case-title" className="block text-sm text-slate-300">Case title</label>
                  <input id="case-title" className="field" placeholder="e.g. Exchange deposit review" required maxLength={200} value={caseTitle} onChange={(event) => setCaseTitle(event.target.value)} />
                  <button className="button w-full" disabled={addCase.isPending}><Plus size={16} />{addCase.isPending ? "Creating..." : "Create investigation case"}</button>
                </form>
              </Card>
              <Card>
                <div className="mb-4 flex items-center justify-between"><div><p className="label">Case register</p><h2 className="mt-1 text-lg font-semibold">Recent investigations</h2></div><FolderKanban className="text-sky-300" size={18} /></div>
                {cases.length === 0 ? <EmptyState text="No cases created yet." /> : (
                  <div className="divide-y divide-slate-800/70">
                    {cases.slice(0, 8).map((item) => <CaseRow key={item.id} item={item} />)}
                  </div>
                )}
              </Card>
            </section>

            <section id="transactions">
              <Card>
                <div className="mb-4 flex items-center justify-between"><div><p className="label">Provider observations</p><h2 className="mt-1 text-lg font-semibold">Recently stored transactions</h2></div><Blocks className="text-teal-300" size={18} /></div>
                {transactions.length === 0 ? <EmptyState text="No transactions stored. Look up a Bitcoin transaction to begin." /> : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[650px] text-left text-sm">
                      <thead className="border-b border-slate-800 text-xs uppercase tracking-wider text-slate-500"><tr><th className="py-3 pr-4 font-medium">Transaction ID</th><th className="py-3 pr-4 font-medium">Block</th><th className="py-3 pr-4 font-medium">Outputs</th><th className="py-3 pr-4 font-medium">Source</th><th className="py-3 font-medium">Observed</th></tr></thead>
                      <tbody className="divide-y divide-slate-800/70">{transactions.map((tx) => <tr key={tx.id}>
                        <td className="max-w-[260px] truncate py-3 pr-4 font-mono text-xs text-sky-200" title={tx.txid}>{tx.txid}</td>
                        <td className="py-3 pr-4 text-slate-400">{tx.block_height?.toLocaleString() ?? "Unconfirmed"}</td>
                        <td className="py-3 pr-4 text-slate-400">{tx.output_count}</td>
                        <td className="py-3 pr-4 text-slate-400">{tx.source}</td>
                        <td className="py-3 text-slate-500">{new Date(tx.observed_at).toLocaleString()}</td>
                      </tr>)}</tbody>
                    </table>
                  </div>
                )}
              </Card>
            </section>
          </>
        )}

        <footer className="flex flex-col gap-2 border-t border-slate-800 py-5 text-xs text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span>ChainGuard AI · Development system · Not for production use</span>
          <span className="flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-amber-400" /> Heuristic detections require analyst review</span>
        </footer>
      </div>
    </main>
  );
}

function StatCard({ label, value, note, icon, tone }: Readonly<{ label: string; value: number; note: string; icon: ReactNode; tone: "amber" | "blue" | "teal" | "violet" }>) {
  const tones = {
    amber: "border-amber-400/25 bg-amber-400/10 text-amber-200",
    blue: "border-sky-400/25 bg-sky-400/10 text-sky-200",
    teal: "border-teal-400/25 bg-teal-400/10 text-teal-200",
    violet: "border-violet-400/25 bg-violet-400/10 text-violet-200",
  };
  return <Card><div className="flex items-start justify-between"><div><p className="label">{label}</p><p className="mt-3 text-3xl font-semibold tabular-nums">{value}</p></div><span className={`rounded-lg border p-2 ${tones[tone]}`}>{icon}</span></div><p className="mt-3 text-xs text-slate-500">{note}</p></Card>;
}

function DataCell({ label, value }: Readonly<{ label: string; value: string }>) {
  return <div><p className="text-xs text-slate-500">{label}</p><p className="mt-1 truncate text-sm text-slate-200" title={value}>{value}</p></div>;
}

function EmptyState({ text }: Readonly<{ text: string }>) {
  return <div className="flex items-center gap-3 rounded-lg border border-dashed border-slate-700 px-4 py-6 text-sm text-slate-500"><CircleHelp size={17} />{text}</div>;
}

function CaseRow({ item }: Readonly<{ item: CaseRecord }>) {
  const tone = item.status === "closed" ? "green" : item.status === "in_review" ? "amber" : "blue";
  return <a href={`/cases/${item.id}`} className="flex items-center justify-between gap-3 py-3 hover:text-sky-200">
    <div className="min-w-0"><p className="truncate text-sm font-medium">{item.title}</p><p className="mt-1 text-xs text-slate-500">Case #{item.id} · Updated {new Date(item.updated_at).toLocaleDateString()}</p></div>
    <div className="flex shrink-0 items-center gap-2"><StatusPill tone={tone}>{item.status.replace("_", " ")}</StatusPill><ChevronRight size={15} className="text-slate-600" /></div>
  </a>;
}
