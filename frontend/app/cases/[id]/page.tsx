"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Download, FileCheck2, FilePlus2, FileSpreadsheet, FileText, ShieldCheck } from "lucide-react";
import { Background, Controls, MarkerType, MiniMap, ReactFlow } from "@xyflow/react";
import { ApiError, api, errorMessage } from "@/lib/api";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import type { Edge as FlowEdge, Node as FlowNode } from "@xyflow/react";

type CaseFlowNode = FlowNode<{ label: ReactNode }>;

export default function CaseDetailPage() {
  const params = useParams<{ id: string }>();
  const caseId = Number(params.id);
  const router = useRouter();
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const [provenance, setProvenance] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [notice, setNotice] = useState("");
  const profile = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  const caseQuery = useQuery({ queryKey: ["case", caseId], queryFn: () => api.caseById(caseId), enabled: Number.isSafeInteger(caseId) && caseId > 0 });
  const graphQuery = useQuery({ queryKey: ["case-graph", caseId], queryFn: () => api.caseGraph(caseId), enabled: caseQuery.isSuccess });
  const eventsQuery = useQuery({ queryKey: ["case-events", caseId], queryFn: () => api.caseEvents(caseId), enabled: caseQuery.isSuccess });
  const evidenceQuery = useQuery({ queryKey: ["case-evidence", caseId], queryFn: () => api.caseEvidence(caseId), enabled: caseQuery.isSuccess });
  const flowGraph = useMemo(() => {
    const graph = graphQuery.data;
    if (!graph) return { nodes: [] as CaseFlowNode[], edges: [] as FlowEdge[] };
    const positions = { input: 0, output: 0, both: 0, transaction: 0 };
    const nodes: CaseFlowNode[] = graph.nodes.map((node) => {
      const direction = node.kind === "transaction" ? "transaction" : node.direction ?? "both";
      const column = positions[direction]++;
      const x = direction === "input" ? 20 : direction === "transaction" ? 360 : 700;
      const displayLabel = node.kind === "transaction"
        ? `${node.label.slice(0, 12)}…`
        : node.label.length > 24 ? `${node.label.slice(0, 12)}…${node.label.slice(-6)}` : node.label;
      const detail = node.kind === "transaction"
        ? `${node.network ?? "Unknown network"} · ${node.source ?? "Unknown source"} · ${node.is_confirmed === true ? "confirmed" : node.is_confirmed === false ? "unconfirmed" : "confirmation unknown"}${node.observed_at ? ` · ${new Date(node.observed_at).toLocaleString()}` : ""}`
        : `Observed script address · ${direction}`;
      return {
        id: node.id,
        position: { x, y: 30 + column * 100 },
        data: {
          label: (
            <span title={node.label} className="block min-w-0">
              <span className="block truncate">{displayLabel}</span>
              <span className="mt-1 block truncate text-[10px] text-slate-400">{detail}</span>
            </span>
          ),
        },
        style: {
          width: 190,
          padding: 10,
          borderRadius: 9,
          border: `1px solid ${node.kind === "transaction" ? "#318bd3" : direction === "input" ? "#35d0bd" : "#b88cff"}`,
          background: node.kind === "transaction" ? "#142130" : "#0c151f",
          color: "#e6edf5",
          fontSize: 12,
        },
        ariaLabel: `${detail}: ${node.label}`,
      };
    });
    const edges: FlowEdge[] = graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      label: edge.value_satoshis === null ? undefined : `${edge.value_satoshis.toLocaleString()} sat`,
      markerEnd: { type: MarkerType.ArrowClosed, color: edge.direction === "input" ? "#35d0bd" : "#b88cff" },
      style: { stroke: edge.direction === "input" ? "#35d0bd" : "#b88cff", strokeWidth: 1.5 },
      labelStyle: { fill: "#94a3b8", fontSize: 10 },
      labelBgStyle: { fill: "#0b141e" },
    }));
    return { nodes, edges };
  }, [graphQuery.data]);

  useEffect(() => {
    if (profile.error instanceof ApiError && profile.error.status === 401) router.replace("/login");
  }, [profile.error, router]);

  const noteMutation = useMutation({
    mutationFn: () => api.addCaseNote(caseId, note.trim()),
    onSuccess: () => {
      setNote("");
      setNotice("Investigation note added.");
      void queryClient.invalidateQueries({ queryKey: ["case-events", caseId] });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });
  const evidenceMutation = useMutation({
    mutationFn: (form: FormData) => api.uploadEvidence(caseId, form),
    onSuccess: () => {
      setFile(null);
      setProvenance("");
      setNotice("Evidence uploaded and SHA-256 recorded.");
      void queryClient.invalidateQueries({ queryKey: ["case-evidence", caseId] });
      void queryClient.invalidateQueries({ queryKey: ["case-events", caseId] });
    },
    onError: (error) => setNotice(errorMessage(error)),
  });

  async function submitNote(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (note.trim()) noteMutation.mutate();
  }

  async function submitEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || !provenance.trim()) return;
    if (file.size > 10 * 1024 * 1024) {
      setNotice("Evidence files are limited to 10 MiB.");
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("source_provenance", provenance.trim());
    evidenceMutation.mutate(form);
  }

  async function downloadEvidence(evidenceId: number, filename: string) {
    try {
      const response = await fetch(`/api/proxy/cases/${caseId}/evidence/${evidenceId}/download`, { credentials: "same-origin" });
      if (!response.ok) {
        const result = await response.json() as { detail?: string };
        throw new Error(result.detail ?? `Download failed (${response.status})`);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      void queryClient.invalidateQueries({ queryKey: ["case-events", caseId] });
      setNotice("Evidence integrity verified before download.");
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  async function exportReport(format: "json" | "csv") {
    try {
      const response = await fetch(`/api/proxy/cases/${caseId}/report${format === "csv" ? ".csv" : ""}`, { credentials: "same-origin" });
      if (!response.ok) {
        const result = await response.json() as { detail?: string };
        throw new Error(result.detail ?? `Report export failed (${response.status})`);
      }
      const report = await response.blob();
      const url = URL.createObjectURL(report);
      const link = document.createElement("a");
      link.href = url;
      link.download = `case-${caseId}-report.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice(`${format.toUpperCase()} case report exported.`);
    } catch (error) {
      setNotice(errorMessage(error));
    }
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
  }

  if (!Number.isSafeInteger(caseId) || caseId <= 0) return <main className="p-8 text-rose-200">Invalid case identifier.</main>;
  if (caseQuery.isPending) return <main className="p-8 text-slate-400">Loading investigation...</main>;
  if (caseQuery.error) return <main className="mx-auto max-w-3xl p-8"><p role="alert" className="text-rose-200">Could not load this case: {errorMessage(caseQuery.error)}</p><a className="button secondary mt-4" href="/dashboard"><ArrowLeft size={15} />Back to overview</a></main>;
  const currentCase = caseQuery.data;

  return (
    <main className="mx-auto min-h-screen max-w-6xl space-y-6 px-5 py-7 lg:px-8">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
        <div>
          <a href="/dashboard" className="mb-4 inline-flex items-center gap-2 text-sm text-slate-400 hover:text-white"><ArrowLeft size={15} />Overview</a>
          <div className="flex items-center gap-2 text-xs uppercase tracking-[0.14em] text-sky-300"><ShieldCheck size={14} /> Case #{currentCase.id}</div>
          <h1 className="mt-2 text-3xl font-semibold">{currentCase.title}</h1>
          <p className="mt-2 text-sm text-slate-400">{currentCase.description || "No case description provided."}</p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-3 py-1.5 text-xs text-sky-200">{currentCase.status.replace("_", " ")}</span>
          <button type="button" className="button secondary" onClick={() => exportReport("json")}><Download size={15} />Export JSON report</button>
          <button type="button" className="button secondary" onClick={() => exportReport("csv")}><FileSpreadsheet size={15} />Export CSV report</button>
        </div>
      </header>

      {notice && <p role="status" className="rounded-lg border border-sky-400/20 bg-sky-400/10 px-4 py-3 text-sm text-sky-100">{notice}</p>}

      <section className="grid gap-5 lg:grid-cols-2">
        <article className="panel p-5">
          <h2 className="text-lg font-semibold">Investigator note</h2>
          <p className="mt-1 text-sm text-slate-400">Notes are attributed to your authenticated account and added to the case activity history.</p>
          <form onSubmit={submitNote} className="mt-4 space-y-3">
            <label htmlFor="note" className="sr-only">Investigation note</label>
            <textarea id="note" className="field min-h-32 resize-y" maxLength={20_000} required value={note} onChange={(event) => setNote(event.target.value)} placeholder="Record observations, methods, and limitations..." />
            <button type="submit" className="button" disabled={noteMutation.isPending || !note.trim()}>{noteMutation.isPending ? "Saving..." : "Add note"}</button>
          </form>
        </article>

        <article className="panel p-5">
          <h2 className="text-lg font-semibold">Attach evidence</h2>
          <p className="mt-1 text-sm text-slate-400">Files are stored in the configured evidence volume. The server computes and verifies SHA-256.</p>
          <form onSubmit={submitEvidence} className="mt-4 space-y-3">
            <label htmlFor="evidence-file" className="block text-sm text-slate-300">Evidence file <span className="text-slate-500">(up to 10 MiB)</span></label>
            <input id="evidence-file" className="field file:mr-3 file:border-0 file:bg-transparent file:text-sky-200" type="file" required onChange={onFileChange} />
            <label htmlFor="provenance" className="block text-sm text-slate-300">Source and collection provenance</label>
            <textarea id="provenance" className="field min-h-20 resize-y" maxLength={4000} required value={provenance} onChange={(event) => setProvenance(event.target.value)} placeholder="Describe the source, collection time, and method..." />
            <button type="submit" className="button" disabled={evidenceMutation.isPending || !file || !provenance.trim()}><FilePlus2 size={15} />{evidenceMutation.isPending ? "Uploading..." : "Upload evidence"}</button>
          </form>
        </article>
      </section>

      <section className="panel p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="label">Case-scoped transaction graph</p>
            <h2 className="mt-1 text-lg font-semibold">Observed address connections</h2>
            <p className="mt-1 text-sm text-slate-400">Only transactions linked to this case are shown; no additional hops are fetched.</p>
          </div>
          {graphQuery.isSuccess && <span className="text-xs text-slate-500">{graphQuery.data.nodes.length} nodes · {graphQuery.data.edges.length} observed flows</span>}
        </div>
        <p className="mb-4 rounded-lg border border-amber-400/20 bg-amber-400/5 px-3 py-2 text-sm text-amber-100">
          Address connectivity does not establish common ownership or real-world attribution.
        </p>
        {graphQuery.isPending ? (
          <p className="rounded-lg border border-dashed border-slate-700 px-4 py-10 text-center text-sm text-slate-500">Loading case graph...</p>
        ) : graphQuery.error ? (
          <div role="alert" className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-4 text-sm text-rose-200">
            <p>Could not load the case graph: {errorMessage(graphQuery.error)}</p>
            <button type="button" className="button secondary mt-3" onClick={() => void graphQuery.refetch()}>Retry</button>
          </div>
        ) : graphQuery.data.nodes.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-700 px-4 py-10 text-center text-sm text-slate-500">No transactions are linked to this case yet. Link a transaction to build a graph from its recorded provider observation.</p>
        ) : (
          <>
            {graphQuery.data.truncated && <p role="status" className="mb-3 text-sm text-amber-200">This graph reached a display limit and is truncated. Review the linked transactions and query again as needed.</p>}
            <div className="h-[520px] overflow-hidden rounded-lg border border-slate-800 bg-[#0b141e]">
              <ReactFlow
                nodes={flowGraph.nodes}
                edges={flowGraph.edges}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                minZoom={0.2}
                maxZoom={1.5}
                nodesDraggable={false}
                nodesConnectable={false}
                proOptions={{ hideAttribution: true }}
              >
                <Background color="#294052" gap={22} />
                <MiniMap nodeColor={(node) => node.id.startsWith("transaction:") ? "#318bd3" : "#35d0bd"} maskColor="rgb(9 15 23 / 70%)" />
                <Controls />
              </ReactFlow>
            </div>
            <ul className="mt-3 space-y-1 text-xs text-slate-500">
              {graphQuery.data.limitations.map((limitation) => <li key={limitation}>• {limitation}</li>)}
            </ul>
          </>
        )}
      </section>

      <section className="panel p-5">
        <div className="mb-4 flex items-center justify-between"><div><p className="label">Evidence register</p><h2 className="mt-1 text-lg font-semibold">Integrity-checked files</h2></div><FileCheck2 size={18} className="text-teal-300" /></div>
        {evidenceQuery.isPending ? <p className="text-sm text-slate-500">Loading evidence...</p> : evidenceQuery.error ? <p role="alert" className="text-sm text-rose-200">{errorMessage(evidenceQuery.error)}</p> : evidenceQuery.data?.length ? (
          <div className="space-y-3">{evidenceQuery.data.map((item) => <div key={item.id} className="rounded-lg border border-slate-800 bg-[#0b141e] p-4">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div className="min-w-0"><p className="flex items-center gap-2 text-sm font-medium"><FileText size={15} className="shrink-0 text-slate-500" />{item.filename}</p><p className="mt-2 break-all font-mono text-[11px] text-teal-200">SHA-256 {item.sha256}</p><p className="mt-1 text-xs text-slate-500">{item.byte_size.toLocaleString()} bytes · {item.source_provenance}</p></div><button className="button secondary shrink-0" onClick={() => downloadEvidence(item.id, item.filename)}><Download size={14} />Verify &amp; download</button></div>
          </div>)}</div>
        ) : <p className="rounded-lg border border-dashed border-slate-700 px-4 py-6 text-sm text-slate-500">No evidence attached to this case.</p>}
      </section>

      <section className="panel p-5">
        <p className="label">Case history</p><h2 className="mt-1 text-lg font-semibold">Activity timeline</h2>
        {eventsQuery.isPending ? <p className="mt-4 text-sm text-slate-500">Loading activity...</p> : eventsQuery.error ? <p role="alert" className="mt-4 text-sm text-rose-200">{errorMessage(eventsQuery.error)}</p> : (
          <ol className="mt-5 space-y-4">{(eventsQuery.data ?? []).map((event) => <li key={event.id} className="flex gap-3">
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-sky-400" />
            <div className="min-w-0 flex-1 border-b border-slate-800 pb-4"><div className="flex flex-wrap justify-between gap-2"><p className="text-sm font-medium text-slate-200">{event.event_type.replaceAll("_", " ")}</p><time className="text-xs text-slate-500">{new Date(event.created_at).toLocaleString()}</time></div><p className="mt-1 break-words text-xs text-slate-500">User #{event.user_id} · {event.details}</p></div>
          </li>)}</ol>
        )}
      </section>
    </main>
  );
}
