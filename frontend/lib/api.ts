export type Alert = {
  id: number;
  transaction_id: number;
  risk_assessment_id: number;
  severity: "medium" | "high" | "critical";
  status: "open" | "acknowledged" | "investigating" | "dismissed" | "closed";
  assigned_user_id: number | null;
  created_at: string;
};

export type AlertFilters = {
  status?: Alert["status"];
  severity?: Alert["severity"];
  network?: string;
  assigned_user_id?: number;
  unassigned?: boolean;
  created_after?: string;
  created_before?: string;
};

export type CaseRecord = {
  id: number;
  title: string;
  description: string | null;
  status: "open" | "in_review" | "closed";
  assigned_user_id: number | null;
  created_at: string;
  updated_at: string;
};

export type CaseGraph = {
  case_id: number;
  nodes: {
    id: string;
    kind: "transaction" | "address";
    label: string;
    txid: string | null;
    address: string | null;
    network: string | null;
    source: string | null;
    observed_at: string | null;
    is_confirmed: boolean | null;
    direction: "input" | "output" | "both" | null;
  }[];
  edges: {
    id: string;
    source: string;
    target: string;
    direction: "input" | "output";
    value_satoshis: number | null;
  }[];
  transaction_count: number;
  truncated: boolean;
  limitations: string[];
};

export type Transaction = {
  id: number;
  network: string;
  txid: string;
  block_height: number | null;
  is_confirmed: boolean | null;
  fee_satoshis: number | null;
  input_count: number;
  output_count: number;
  output_total_satoshis: number;
  source: string;
  observed_at: string;
};

export type EthereumTransaction = {
  id: number;
  chain_id: number;
  tx_hash: string;
  block_number: number | null;
  block_hash: string | null;
  receipt_status: "success" | "reverted" | null;
  from_address: string;
  to_address: string | null;
  value_wei: string;
  gas_limit: number;
  gas_used: number | null;
  effective_gas_price_wei: string | null;
  raw_observation: Record<string, unknown>;
  source: string;
  observed_at: string;
  updated_at: string;
};

export type EthereumAddressSnapshot = {
  chain_id: number;
  address: string;
  balance_wei: string;
  transaction_count: number;
  block_number: number;
  block_hash: string;
  source: string;
  retrieved_at: string;
};

export type BitcoinAddressStats = {
  funded_txo_count: number;
  funded_txo_sum: number;
  spent_txo_count: number;
  spent_txo_sum: number;
  tx_count: number;
};

export type BitcoinAddressHistory = {
  address: string;
  chain_stats: BitcoinAddressStats;
  mempool_stats: BitcoinAddressStats;
  transactions: {
    txid: string;
    block_height: number | null;
    is_confirmed: boolean | null;
    fee_satoshis: number;
    input_count: number;
    output_count: number;
    output_total_satoshis: number;
  }[];
  next_cursor: string | null;
  source: string;
  retrieved_at: string;
};

export type RiskAssessment = {
  id: number;
  transaction_id: number;
  score: number;
  category: "low" | "medium" | "high" | "critical";
  model_version: string;
  calibrated: boolean;
  explanation: { indicator: string; weight: number; [key: string]: unknown }[];
  recommendation: string;
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(typeof init?.body === "string" ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
    credentials: "same-origin",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null;
    throw new ApiError(body?.detail ?? `Request failed (${response.status})`, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  me: () => request<{ id: number; email: string; role: string }>("/api/auth/me"),
  logout: () => request<void>("/api/auth/logout", { method: "POST" }),
  alerts: (filters: AlertFilters = {}) => {
    const query = new URLSearchParams({ limit: "100" });
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return request<Alert[]>(`/api/proxy/alerts?${query.toString()}`);
  },
  cases: () => request<CaseRecord[]>("/api/proxy/cases?limit=100"),
  transactions: () => request<Transaction[]>("/api/proxy/transactions?limit=20"),
  lookupTransaction: (txid: string, network: "bitcoin" | "ethereum" = "bitcoin") =>
    network === "ethereum"
      ? request<EthereumTransaction>(`/api/proxy/transactions/ethereum/${encodeURIComponent(txid)}`)
      : request<Transaction>(`/api/proxy/transactions/bitcoin/${encodeURIComponent(txid)}`),
  lookupBitcoinAddress: (address: string, cursor?: string) => {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
    return request<BitcoinAddressHistory>(
      `/api/proxy/addresses/bitcoin/${encodeURIComponent(address)}${query}`,
    );
  },
  lookupEthereumAddress: (address: string) =>
    request<EthereumAddressSnapshot>(`/api/proxy/addresses/ethereum/${encodeURIComponent(address)}`),
  analyze: (txid: string) =>
    request<RiskAssessment>("/api/proxy/risk/analyze", { method: "POST", body: JSON.stringify({ txid }) }),
  createCase: (title: string) =>
    request<CaseRecord>("/api/proxy/cases", { method: "POST", body: JSON.stringify({ title }) }),
  updateAlert: (id: number, status: Alert["status"]) =>
    request<Alert>(`/api/proxy/alerts/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  caseById: (id: number) => request<CaseRecord>(`/api/proxy/cases/${id}`),
  caseGraph: (id: number) => request<CaseGraph>(`/api/proxy/cases/${id}/graph`),
  caseEvents: (id: number) => request<{ id: number; user_id: number; event_type: string; details: string; created_at: string }[]>(`/api/proxy/cases/${id}/events`),
  caseEvidence: (id: number) => request<{ id: number; filename: string; sha256: string; byte_size: number; source_provenance: string; created_at: string }[]>(`/api/proxy/cases/${id}/evidence`),
  addCaseNote: (id: number, content: string) =>
    request<{ id: number }>(`/api/proxy/cases/${id}/notes`, { method: "POST", body: JSON.stringify({ content }) }),
  uploadEvidence: (id: number, form: FormData) =>
    request<{ id: number; filename: string; sha256: string; byte_size: number }>(`/api/proxy/cases/${id}/evidence`, { method: "POST", body: form }),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}
