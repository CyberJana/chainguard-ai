# System architecture

## Status

This document defines the target boundaries for phased implementation. It does not assert that the planned services, access controls, or workflows already exist. The current Compose setup provides local PostgreSQL, Redis, and an initial FastAPI service. Its schema includes identity, public Bitcoin and Ethereum transaction observations, heuristic risk assessments, alerts, case activity, evidence metadata, and one-time invitations; MFA, Ethereum risk/case workflows, streaming ingestion, and calibrated models are not implemented.

## Components and ownership

- **Web application:** Next.js/TypeScript UI. It presents API results and analyst actions, but is not an authorization boundary.
- **API:** FastAPI service with versioned REST endpoints. It validates inputs, authenticates users, enforces organization and role permissions, and records security-relevant activity.
- **PostgreSQL:** Authoritative store for organizations, users, normalized blockchain observations, risk assessments, alerts, investigations, evidence metadata, and audit events. Tenant identity must be included in access paths and protected by server-side checks; PostgreSQL row-level security may be added as defense in depth.
- **Redis and workers:** Redis is a queue/cache dependency, not the source of truth. Background processing must be idempotent and safe to retry.
- **Blockchain adapters:** Provider-specific code normalizes public observations into a provider-independent representation and retains provider, network, source timestamp, and retrieval provenance.
- **Risk analysis:** Deterministic rules and versioned ML inference produce bounded triage scores and explanations. The system must retain the model/rule version and must not imply that risk equals proven wrongdoing. An offline Isolation Forest training/evaluation utility exists, but no real dataset has been trained, its artifact is not loaded by API inference, and no score calibration is claimed.
- **Optional graph store:** Derived graph projections only. PostgreSQL remains authoritative, and graph results must be traceable to source observations.
- **External intelligence:** Integrations are allowlisted/configured, attributed to source, licensed or otherwise permitted, and timestamped with confidence and expiry metadata.

The initial authentication implementation uses Argon2 password hashes and opaque, random bearer tokens. Only token digests are persisted, and logout revokes the presented session. Initial account provisioning is an interactive one-time CLI bootstrap; self-registration, password-reset, and MFA workflows are not implemented. Administrators can create users and issue one-time invitations expiring after 24 hours. Authentication is required for application APIs, but a complete role matrix and resource-level authorization policy remain future work.

Bitcoin transaction data is retrieved from the configured HTTPS provider adapter. Each distinct provider payload is retained as a separate observation hash to preserve changes such as confirmation/reorganization updates. Authenticated address-history lookups validate provider summaries and transaction pages and return up to 25 entries per cursor page with source and retrieval timestamp; these live address results are not persisted. Ethereum transaction/receipt data is retrieved through an optional configured HTTPS JSON-RPC endpoint and persisted in separate tables with chain ID and wei/gas-native fields; distinct provider observations are hash-preserved. The provider result is validated, but its chain ID is accepted as reported and is not pinned to a single configured network. Current normalized chain transaction rows are shared public-chain data; risk assessments, alerts, cases, notes, and evidence are organization-scoped. Risk analysis remains Bitcoin-only, explicitly uncalibrated, and must not be presented as a probability or confirmation of wrongdoing.

Ethereum receipt status is preserved as `success`, `reverted`, or unknown when the receipt is not yet available. Transaction value and gas price are stored as decimal strings in wei to avoid 64-bit overflow; gas counts and chain/block numbers use bounded signed 64-bit database integers. Ethereum lookups do not yet support address history, case linking, graphing, risk scoring, or reorganization-aware block ingestion.

Alert listing supports server-side filters for status, severity, network, assigned or unassigned state, assignee ID, and a timezone-aware creation-time range. The organization predicate is always applied independently of optional client filters; pagination remains bounded.

JSON and CSV case reports use the same tenant-scoped report data, include provider observations, analyst notes, evidence hashes/provenance, activity, and chain-of-custody events, and record export format in the audit log. CSV cells beginning with spreadsheet formula characters are prefixed to reduce formula-injection risk; CSV is a convenience export, not a signed or tamper-evident forensic package.

The case graph endpoint reads only raw provider observations for transactions already linked to the requested organization-scoped case. It models observed input `prevout.scriptpubkey_address` values as address-to-transaction edges and output `scriptpubkey_address` values as transaction-to-address edges. Transaction nodes retain their provider source and observation timestamp. Results are capped at 25 transactions, 250 total nodes, and 500 edges; missing or malformed address fields are omitted, and graph access is audit logged. Connectivity is not evidence of common ownership or real-world attribution, and the graph does not discover additional hops or persist a separate graph projection.

## Data flow

1. A configured provider adapter retrieves public chain data or a background job accepts a requested lookup.
2. The normalizer validates and deduplicates observations while preserving source identifiers and timestamps.
3. Persisted observations are analyzed with versioned rules/models; raw data remains distinguishable from derived indicators and analyst conclusions.
4. Risk findings can create or update alerts. Analysts may associate observations and derived findings with cases.
5. Evidence exports include provenance and integrity hashes; custody events record who performed an evidence action and when.
6. API access and material case/alert actions are audit logged with actor and organization context.

Reorganization-aware ingestion must be designed around canonical block references and reversible projections. It must not silently overwrite prior observations or claim that an unconfirmed transaction is final.

## Security and trust boundaries

- Treat all client input, chain-provider responses, and third-party intelligence as untrusted.
- Enforce authentication, role permissions, and organization scoping in API/service/data-access paths; UI filtering is not sufficient.
- Never collect seed phrases, private keys, or credentials for wallet custody.
- Bind local database ports to loopback. Production deployments must use private networking, TLS, managed secret injection, least-privilege accounts, and restricted administrative access.
- Do not log passwords, tokens, private keys, or unnecessary personal data. Keep audit records append-oriented and access controlled; tamper-evidence and retention guarantees require implementation and testing.
- Validate provider URLs and outbound destinations before enabling external integrations; use explicit configuration rather than accepting arbitrary user-supplied endpoints.

## Planned repository boundaries

```text
backend/         API, domain services, persistence, migrations, jobs
frontend/        Next.js application and API client
blockchain/      provider adapters, normalized chain data
ml/              features, training, evaluation, versioned inference
tests/           unit, integration, and end-to-end suites
docs/            architecture, setup, operations, and limitations
infrastructure/  deployment, CI, monitoring, backup configuration
```

Modules should depend inward on domain interfaces. Provider-specific formats and optional infrastructure should not leak into case-management or risk-domain logic.

## Implementation sequence

The implementation sequence and status are tracked in the root [README](../README.md). Database entities and API contracts should be designed with tenant ownership, provenance, idempotency, and auditability before adding their corresponding workflows.
