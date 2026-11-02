# ChainGuard AI

ChainGuard AI is planned as a blockchain transaction monitoring, risk triage, and digital-forensics investigation platform. It is intended to analyze public blockchain information; it will not custody funds or request wallet seed phrases.

> **Implementation status:** The repository includes an initial FastAPI/PostgreSQL backend for identity, Bitcoin transaction and live address-history lookups, read-only Ethereum transaction lookup, uncalibrated Bitcoin rule-based risk triage, alerts, investigation cases, SHA-256-verified evidence uploads, and a bounded case-scoped Bitcoin transaction graph. MFA, Ethereum risk/case workflows, trained/calibrated models, rate limiting, and production hardening are not complete. This is not production-ready and must not be used for operational investigations or decisions.

## Planned capabilities

- Bitcoin and Ethereum data ingestion from configured public data providers.
- Explainable risk indicators with calibrated scores, model/version provenance, and a clear distinction between suspicious activity and confirmed fraud.
- Analyst-managed alerts, investigations, evidence provenance, chain of custody, and reports.
- Organization-scoped access controls, audit trails, and operational monitoring.

The Bitcoin adapter uses a configured HTTPS API compatible with mempool.space to retrieve individual transactions and paginated address history. Individual transaction payloads are persisted as distinct observations; address-history pages are validated and returned live with provider provenance, but are not persisted. Ethereum supports read-only transaction/receipt lookup and a live address snapshot (native balance and transaction count pinned to one latest block) through an optional configured HTTPS JSON-RPC endpoint. Ethereum observations use separate persistence and retain native wei/gas values and chain IDs; Ethereum transaction history, risk scoring, case linking, graphing, and block ingestion are not implemented. An offline, reproducible Isolation Forest training/evaluation pipeline is available for operator-supplied, labeled data; no real-world dataset has been trained or evaluated, the model is not connected to API scoring, and scores are not calibrated. Block ingestion, reorganization orchestration, streaming ingestion, and external threat-intelligence integrations remain future work. The deployed Bitcoin rule score is explicitly uncalibrated and must not be interpreted as a fraud probability.

The planned system boundaries, trust assumptions, and project layout are described in [docs/architecture.md](docs/architecture.md). These are design targets, not claims about implemented controls.

## Architecture

The target system is a modular monorepo. A versioned FastAPI service owns the initial identity endpoints and will grow to own tenant-scoped persistence, blockchain normalization, risk analysis, case workflows, and audit events. A Next.js application will consume that API. PostgreSQL is the source of truth; Redis is reserved for future background work and short-lived coordination. Optional graph storage and external providers will be isolated behind adapters.

```mermaid
flowchart LR
    Analyst[Analyst browser] --> Web[Next.js web app]
    Web --> API[FastAPI REST API]
    API --> DB[(PostgreSQL)]
    API --> Queue[(Redis)]
    Queue --> Worker[Background workers]
    Worker --> Providers[Configured public chain providers]
    Worker --> DB
    API --> ML[Versioned risk analysis]
    ML --> DB
```

## Local development infrastructure

### Prerequisites

- Windows 10/11 with Docker Desktop configured for Linux containers (WSL 2 backend recommended), or a compatible Docker Engine with Docker Compose v2.
- Git.

Docker Compose provisions PostgreSQL, Redis, and the API. The API image installs the pinned Python dependencies and applies Alembic migrations before starting. The frontend is a separate Next.js application and can run locally against the API. Backend tests and frontend typecheck/build have been run using temporary local Python and Node toolchains; Docker Compose runtime validation has not been run.

### Start services on Windows

From PowerShell in the repository root:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d
docker compose ps
```

Replace the example password in both `POSTGRES_PASSWORD` and `DATABASE_URL` with the same URL-safe, local-only secret before starting. The sample is a placeholder, not a credential. Ports bind to `127.0.0.1` so services are not exposed to the local network by default. The initial API health endpoint is `http://127.0.0.1:8000/api/v1/system/health`.

Create the first organization administrator after the API is healthy:

```powershell
docker compose exec api python -m app.bootstrap
```

The bootstrap command is permitted only while the database has no organization or user. It prompts for the organization, administrator email, and a 14-character minimum password; the password is hashed and is never placed in an environment variable.

Login uses `POST /api/v1/auth/login` with JSON `{"email":"...","password":"..."}`. Pass the returned bearer token to `GET /api/v1/auth/me`; `POST /api/v1/auth/logout` revokes only that session. Tokens are opaque random values; only their SHA-256 digests are stored.

The authenticated API currently also exposes:

| Method and path | Purpose |
| --- | --- |
| `GET /api/v1/system/health` | Database-backed service health |
| `POST /api/v1/auth/accept-invitation` | Accept a one-time organization invitation |
| `/api/v1/admin/users` and `/api/v1/admin/invitations` | Administrator-scoped user management and invitation issuance |
| `GET /api/v1/transactions` | Paginated stored public transaction observations |
| `GET /api/v1/transactions/bitcoin/{txid}` | Fetch and persist a transaction from the configured provider |
| `GET /api/v1/transactions/ethereum/{tx_hash}` | Fetch Ethereum transaction and receipt via optional configured JSON-RPC provider |
| `GET /api/v1/addresses/ethereum/{address}` | Fetch a provider-reported balance and transaction-count snapshot pinned to one latest block |
| `GET /api/v1/addresses/bitcoin/{address}` | Fetch live address totals and a 25-transaction history page; pass `cursor` for older pages |
| `POST /api/v1/risk/analyze` | Analyze an ingested transaction using explicitly uncalibrated rules |
| `GET /api/v1/alerts` / `PATCH /api/v1/alerts/{id}` | Organization-scoped alert triage; filter by status, severity, network, assignee, and timezone-aware creation range |
| `/api/v1/cases` | Organization-scoped case CRUD, notes, transaction links, events, JSON/CSV reports, and bounded graph from linked transaction observations |
| `GET /api/v1/cases/{id}/graph` | Audited case graph with directional address-to-transaction flows; capped at 25 transactions, 250 total nodes, and 500 edges |
| `/api/v1/cases/{id}/evidence` | Upload/list case evidence; uploads are limited to 10 MiB |
| `/api/v1/cases/{id}/evidence/{evidence_id}/download` | Verify SHA-256 before attachment download and record custody event |

These endpoints are a starting point, not a complete API contract. Organization scoping is enforced in case/alert/risk queries; blockchain observations contain public chain data and are shared across tenants. Alert filters are applied server-side and remain restricted to the current organization. The API does not yet include rate limiting, MFA, password reset, or a full role-specific permission policy. User administration and invite creation require the administrator role; invitations expire after 24 hours and are accepted once.

To stop the services while retaining database state:

```powershell
docker compose down
```

To remove the local database contents as well, use `docker compose down --volumes`. This permanently deletes the Compose-managed local database volume.

### Backend tests

The tests use an in-memory SQLite database and do not require Docker services. With Python 3.12 installed:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-test.txt
python -m pytest
```

Direct dependency versions are pinned in the requirements files; a generated transitive lockfile is still outstanding. The latest backend test run passed all 19 tests. Frontend typecheck and production build are also validated; the test suite uses an in-memory SQLite database and does not require Docker.

### Offline ML baseline

The training pipeline uses operator-provided Bitcoin transaction CSV data and does not download or fabricate a dataset. The CSV must contain `observed_at` (timezone-aware ISO 8601), unique 64-character hexadecimal `txid`, non-negative integer `input_count`, `output_count`, `output_total_satoshis`, and `fee_satoshis`, and `label` (`0` = not labeled suspicious, `1` = labeled suspicious; `0` is not proof of benign activity). Rows are split chronologically 80/20; Isolation Forest fits on the earlier unlabeled features and the later labels are used only for evaluation. Dataset source and license are mandatory metadata. The pipeline reports holdout average precision, precision, recall, F1, and confusion-matrix counts, but it does not calibrate scores, establish real-world model performance, or integrate model artifacts into API risk decisions.

From the repository root in PowerShell, install the pinned ML dependency and run against a dataset whose source and permitted use you have verified:

```powershell
py -3.12 -m pip install -r ml/requirements.txt
py -3.12 -m ml.train_iforest --dataset .\data\licensed-bitcoin.csv --dataset-source "Dataset name and citation" --dataset-license "License or permitted-use terms" --model-version "iforest-2026-01"
```

The command writes a compressed `joblib` artifact and a JSON provenance/evaluation report under `artifacts\ml`. The artifact is not loaded by the application; only load artifacts generated from trusted sources because loading untrusted `joblib` files can execute code. Do not treat test-fixture data or pipeline smoke-test metrics as model evidence. No real dataset, training run, calibration exercise, or deployed ML inference is included.

### Frontend

With Node.js 22 installed, create the npm lockfile on first setup, then run the dashboard:

```powershell
cd frontend
Copy-Item .env.example .env.local
npm install
npm run dev
```

Set `BACKEND_API_URL=http://127.0.0.1:8000` in `frontend/.env.local` when launching Next.js from the host. Sign-in is proxied through Next.js, and the opaque API token is held in an HttpOnly, SameSite=Strict cookie rather than browser storage. The UI currently covers login, Bitcoin transaction lookup/risk and address history, Ethereum transaction lookup and account snapshots, alert triage, case creation/details, notes, evidence upload/download, JSON/CSV report export, and interactive case-scoped Bitcoin graph exploration.

### Configuration

`.env.example` documents the variables used by `docker-compose.yml`. Copy it to `.env`; `.env` is ignored by Git. Set `ETHEREUM_PROVIDER_URL` to a trusted HTTPS JSON-RPC endpoint to enable Ethereum transaction and account snapshot lookups; the endpoint may contain provider credentials only in the ignored local `.env`, not in `.env.example`. Never put production credentials, wallet secrets, or real case data in the example file.

## Repository layout

| Path | Purpose |
| --- | --- |
| `backend/` | FastAPI application, initial identity models/API, migrations, and tests |
| `frontend/` | Next.js analyst dashboard and case details |
| `blockchain/` | Provider adapters and normalized chain data (planned) |
| `ml/` | Offline, provenance-recording Isolation Forest training and chronological evaluation baseline |
| `tests/` | Unit, integration, and end-to-end tests (planned) |
| `docs/` | Architecture, operations, data provenance, and limitations |
| `infrastructure/` | Deployment and monitoring configuration (planned) |

Directories are introduced with their implementation rather than empty placeholder files.

## Development phases

Work is staged to keep each layer reviewable and verifiable:

1. Repository setup, architecture, and development environment — **complete (not runtime-validated)**.
2. FastAPI, PostgreSQL schema/migrations, and authentication — **login/session/bootstrap/admin invitation and user management implemented; MFA, password reset, and full RBAC remain**.
3. Bitcoin ingestion and normalization — **individual transaction provider lookup, deduplication, observation history, and live paginated address-history lookup implemented; address pages are not persisted, while block ingestion and reorganization orchestration remain**.
4. Risk analysis — **uncalibrated explainable rules baseline and offline Isolation Forest training/evaluation pipeline implemented; real licensed dataset, evaluated model, API inference integration, and calibration remain**.
5. Alerts, cases, notes, transaction links, evidence hashing/custody, and case reports — **initial APIs implemented; alert filters cover network, status, severity, assignment, and creation time; JSON and CSV reports include provenance and custody records; PDF reports, notifications, and supervisory review remain**.
6. Next.js dashboard and transaction explorer — **initial API-connected dashboard, case details, and case-scoped interactive graph implemented; remaining analyst modules remain**.
7. Graph exploration — **initial case-scoped Bitcoin graph implemented from already-linked raw observations, with bounded output, audited access, and no ownership/identity attribution; multi-hop exploration and Ethereum remain**.
8. Ethereum and reviewed threat-intelligence integrations — **Ethereum transaction/receipt lookup and a latest-block-pinned account balance/nonce snapshot are implemented behind an optional configured JSON-RPC endpoint; wei/gas units and provider provenance are retained; transaction history, Ethereum risk/case/graph workflows, and threat intelligence remain**.
9. Automated testing, security hardening, observability, and performance work — **focused tests and CI added; runtime checks and broader coverage remain**.
10. Deployment preparation and operational documentation — **local Compose setup only; production deployment and backup/restore remain**.

No phase is considered complete until its implementation and relevant checks are present and executed. See [CONTRIBUTING.md](CONTRIBUTING.md) for development expectations.

## Responsible-use and limitations

- Only public blockchain data and properly licensed or permitted intelligence sources are in scope.
- An anomaly or elevated risk score is a triage signal, not proof of fraud or attribution to a person.
- Scores must not be described as fraud probabilities without empirical validation and calibration.
- Demonstration data, when added, must be explicitly labeled synthetic and kept separate from live provider data.
- This repository has not been security-reviewed or validated for production use.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and security boundaries.
