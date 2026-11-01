# System architecture

## Status

This document defines the target boundaries for phased implementation. It does not assert that the planned services, access controls, or workflows already exist. The current Compose setup provides local PostgreSQL, Redis, and an initial FastAPI identity service. Its schema has organizations, users, roles, hashed bearer sessions, and audit events; it does not yet include blockchain, case, evidence, MFA, or invitation workflows.

## Components and ownership

- **Web application:** Next.js/TypeScript UI. It presents API results and analyst actions, but is not an authorization boundary.
- **API:** FastAPI service with versioned REST endpoints. It validates inputs, authenticates users, enforces organization and role permissions, and records security-relevant activity.
- **PostgreSQL:** Authoritative store for organizations, users, normalized blockchain observations, risk assessments, alerts, investigations, evidence metadata, and audit events. Tenant identity must be included in access paths and protected by server-side checks; PostgreSQL row-level security may be added as defense in depth.
- **Redis and workers:** Redis is a queue/cache dependency, not the source of truth. Background processing must be idempotent and safe to retry.
- **Blockchain adapters:** Provider-specific code normalizes public observations into a provider-independent representation and retains provider, network, source timestamp, and retrieval provenance.
- **Risk analysis:** Deterministic rules and versioned ML inference produce bounded triage scores and explanations. The system must retain the model/rule version and must not imply that risk equals proven wrongdoing.
- **Optional graph store:** Derived graph projections only. PostgreSQL remains authoritative, and graph results must be traceable to source observations.
- **External intelligence:** Integrations are allowlisted/configured, attributed to source, licensed or otherwise permitted, and timestamped with confidence and expiry metadata.

The initial authentication implementation uses Argon2 password hashes and opaque, random bearer tokens. Only token digests are persisted, and logout revokes the presented session. Initial account provisioning is an interactive one-time CLI bootstrap; self-registration and invitation workflows are not implemented. The role dependency helper is groundwork for protected routes, not evidence that all application actions are currently authorized.

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
