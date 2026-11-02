import csv
import io
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Alert,
    AuditLog,
    Base,
    BlockchainTransaction,
    EvidenceItem,
    EthereumTransaction,
    EthereumTransactionObservation,
    Organization,
    Role,
    TransactionObservation,
    User,
)
from app.config import Settings
from app.security import hash_password
from app.services.bitcoin import (
    BitcoinAddressHistory,
    BitcoinAddressStats,
    ingest_bitcoin_transaction,
    normalize_bitcoin_address_history,
    normalize_bitcoin_transaction,
)
from app.services.risk import assess_bitcoin_transaction
from app.services.ethereum import normalize_ethereum_transaction

engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Generator[TestClient, None, None]:
    from app.routes import cases

    Base.metadata.create_all(engine)
    monkeypatch.setattr(cases.settings, "evidence_storage_path", tmp_path)

    def override_get_db() -> Generator[Session, None, None]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestSession.begin() as db:
        organization = Organization(name="Test Org")
        db.add(organization)
        db.flush()
        db.add(User(organization_id=organization.id, email="analyst@example.com", password_hash=hash_password(PASSWORD), role=Role.ANALYST.value))
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def token_for(client: TestClient, email: str = "analyst@example.com") -> str:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_bitcoin_normalization_preserves_confirmed_metadata() -> None:
    txid = "a" * 64
    normalized = normalize_bitcoin_transaction(
        txid,
        {
            "txid": txid,
            "fee": 1000,
            "vin": [{"txid": "b" * 64}],
            "vout": [{"value": 20_000}, {"value": 30_000}],
            "status": {"confirmed": True, "block_height": 100, "block_hash": "c" * 64, "block_time": 1_700_000_000},
        },
    )
    assert normalized.input_count == 1
    assert normalized.output_count == 2
    assert normalized.output_total_satoshis == 50_000
    assert normalized.block_height == 100
    assert normalized.is_confirmed is True


def test_bitcoin_normalization_rejects_mismatched_hash_and_invalid_value() -> None:
    with pytest.raises(ValueError):
        normalize_bitcoin_transaction("a" * 64, {"txid": "b" * 64, "fee": 0, "vin": [], "vout": [], "status": {}})
    with pytest.raises(ValueError):
        normalize_bitcoin_transaction("a" * 64, {"txid": "a" * 64, "fee": 0, "vin": [], "vout": [{"value": -1}], "status": {}})


def test_bitcoin_address_history_normalizes_page_and_rejects_provider_mismatch() -> None:
    address = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    txid = "9" * 64
    stats = {
        "funded_txo_count": 2,
        "funded_txo_sum": 1_000,
        "spent_txo_count": 1,
        "spent_txo_sum": 500,
        "tx_count": 2,
    }
    summary = {"address": address, "chain_stats": stats, "mempool_stats": {key: 0 for key in stats}}
    transaction = {
        "txid": txid,
        "fee": 100,
        "vin": [{"txid": "a" * 64}],
        "vout": [{"value": 500, "scriptpubkey_address": address}],
        "status": {"confirmed": True, "block_height": 100},
    }
    chain_stats, mempool_stats, transactions = normalize_bitcoin_address_history(
        address, summary, [transaction]
    )
    assert chain_stats.tx_count == 2
    assert mempool_stats.tx_count == 0
    assert transactions[0].txid == txid
    with pytest.raises(ValueError):
        normalize_bitcoin_address_history("1wrong", summary, [transaction])
    with pytest.raises(ValueError):
        normalize_bitcoin_address_history(
            address, summary, [{**transaction, "fee": -1}]
        )
    with pytest.raises(ValueError):
        normalize_bitcoin_address_history(
            address,
            summary,
            [{**transaction, "vout": [{"value": 500, "scriptpubkey_address": "1unrelated"}]}],
        )


def test_bitcoin_address_history_fetches_summary_and_cursor_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import app.services.bitcoin as bitcoin

    address = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    cursor = "f" * 64
    stats = {
        "funded_txo_count": 1,
        "funded_txo_sum": 500,
        "spent_txo_count": 0,
        "spent_txo_sum": 0,
        "tx_count": 1,
    }
    summary = {"address": address, "chain_stats": stats, "mempool_stats": stats}
    txs = [
        {
            "txid": f"{index:064x}",
            "fee": 100,
            "vin": [{"prevout": {"scriptpubkey_address": address}}],
            "vout": [{"value": 500}],
            "status": {"confirmed": True, "block_height": index + 1},
        }
        for index in range(25)
    ]
    requested_urls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload: object) -> None:
            self.content = json.dumps(payload).encode()
            self.headers = {"content-length": str(len(self.content))}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def iter_bytes(self):
            return iter((self.content,))

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def stream(self, method: str, url: str, headers: dict) -> FakeResponse:
            requested_urls.append(url)
            return FakeResponse(summary if url.endswith(f"/address/{address}") else txs)

    monkeypatch.setattr(bitcoin.httpx, "Client", FakeClient)
    result = bitcoin.fetch_bitcoin_address_history(address, cursor)
    assert len(result.transactions) == 25
    assert result.next_cursor == txs[-1]["txid"]
    assert requested_urls[1].endswith(f"/address/{address}/txs/chain/{cursor}")
    with pytest.raises(HTTPException) as error:
        bitcoin.fetch_bitcoin_address_history("not-a-bitcoin-address")
    assert error.value.status_code == 422


def test_bitcoin_address_lookup_returns_provider_history_and_audits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routes.transactions as transaction_routes

    address = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    result = BitcoinAddressHistory(
        address=address,
        chain_stats=BitcoinAddressStats(2, 1_000, 1, 500, 2),
        mempool_stats=BitcoinAddressStats(0, 0, 0, 0, 0),
        transactions=(),
        next_cursor=None,
        source="provider.example",
        retrieved_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(transaction_routes, "fetch_bitcoin_address_history", lambda requested, cursor: result)
    response = client.get(
        f"/api/v1/addresses/bitcoin/{address}",
        headers={"Authorization": f"Bearer {token_for(client)}"},
    )
    assert response.status_code == 200
    assert response.json()["address"] == address
    assert response.json()["chain_stats"]["tx_count"] == 2
    with TestSession() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "address.lookup"))
        assert audit is not None
        assert audit.details == f"bitcoin:{address}; source=provider.example"


def test_ingestion_deduplicates_and_preserves_changed_provider_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.bitcoin as bitcoin

    txid = "f" * 64
    unconfirmed = {
        "txid": txid,
        "fee": 1000,
        "vin": [],
        "vout": [{"value": 500}],
        "status": {"confirmed": False},
    }
    confirmed = {
        "txid": txid,
        "fee": 1000,
        "vin": [],
        "vout": [{"value": 500}],
        "status": {"confirmed": True, "block_height": 100, "block_hash": "1" * 64, "block_time": 1_700_000_000},
    }
    payloads = iter([unconfirmed, confirmed, confirmed])
    monkeypatch.setattr(bitcoin, "fetch_bitcoin_transaction", lambda requested: (next(payloads), "test-provider"))

    Base.metadata.create_all(engine)
    with TestSession.begin() as db:
        first = ingest_bitcoin_transaction(db, txid)
        first_id = first.id
        assert first.is_confirmed is False
        second = ingest_bitcoin_transaction(db, txid)
        assert second.id == first_id
        assert second.is_confirmed is True
        ingest_bitcoin_transaction(db, txid)
        observations = list(db.scalars(select(TransactionObservation)))
        assert len(observations) == 2
        assert len({observation.observation_hash for observation in observations}) == 2
    Base.metadata.drop_all(engine)

def test_provider_configuration_requires_https_except_local_development() -> None:
    with pytest.raises(ValueError):
        Settings(environment="development", bitcoin_provider_base_url="http://provider.example/api")
    settings = Settings(environment="development", bitcoin_provider_base_url="http://localhost:3000/api")
    assert settings.bitcoin_provider_base_url == "http://localhost:3000/api"
    assert Settings(environment="development", ethereum_provider_url="").ethereum_provider_url is None
    with pytest.raises(ValueError):
        Settings(environment="development", ethereum_provider_url="http://provider.example/rpc")
    assert Settings(environment="development", ethereum_provider_url="http://localhost:8545").ethereum_provider_url == "http://localhost:8545"


def test_ethereum_normalization_keeps_wei_and_gas_units_and_checks_provider_data() -> None:
    tx_hash = "0x" + "a" * 64
    transaction = {
        "hash": tx_hash,
        "from": "0x" + "b" * 40,
        "to": "0x" + "c" * 40,
        "value": "0xde0b6b3a7640000",
        "gas": "0x5208",
        "blockNumber": "0x10",
        "blockHash": "0x" + "d" * 64,
    }
    receipt = {
        "transactionHash": tx_hash,
        "status": "0x1",
        "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00",
    }
    normalized = normalize_ethereum_transaction(tx_hash, transaction, receipt, "0x1")
    assert normalized["chain_id"] == 1
    assert normalized["block_number"] == 16
    assert normalized["value_wei"] == "1000000000000000000"
    assert normalized["gas_limit"] == 21_000
    assert normalized["gas_used"] == 21_000
    assert normalized["effective_gas_price_wei"] == "1000000000"
    assert normalized["receipt_status"] == "success"
    assert normalized["from_address"] == "0x" + "b" * 40

    with pytest.raises(ValueError):
        normalize_ethereum_transaction(tx_hash, {**transaction, "hash": "0x" + "e" * 64}, receipt, "0x1")
    with pytest.raises(ValueError):
        normalize_ethereum_transaction(tx_hash, {**transaction, "value": "-1"}, receipt, "0x1")
    with pytest.raises(ValueError):
        normalize_ethereum_transaction(tx_hash, {**transaction, "from": 1}, receipt, "0x1")
    with pytest.raises(ValueError):
        normalize_ethereum_transaction(tx_hash, transaction, {**receipt, "status": "0x2"}, "0x1")
    with pytest.raises(ValueError):
        normalize_ethereum_transaction(
            tx_hash,
            transaction,
            {**receipt, "blockNumber": "0x11"},
            "0x1",
        )


def test_ethereum_lookup_persists_provider_observations_and_audits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.ethereum as ethereum

    tx_hash = "0x" + "a" * 64
    transaction = {
        "hash": tx_hash,
        "from": "0x" + "b" * 40,
        "to": "0x" + "c" * 40,
        "value": "0xde0b6b3a7640000",
        "gas": "0x5208",
        "blockNumber": "0x10",
        "blockHash": "0x" + "d" * 64,
    }
    current_receipt = {
        "transactionHash": tx_hash,
        "status": "0x1",
        "gasUsed": "0x5208",
        "effectiveGasPrice": "0x3b9aca00",
    }

    def fake_fetch(requested_hash: str):
        assert requested_hash == tx_hash
        return (
            {"chain_id": "0x1", "transaction": transaction, "receipt": current_receipt.copy()},
            "rpc.example",
        )

    monkeypatch.setattr(ethereum, "fetch_ethereum_transaction", fake_fetch)
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    first = client.get(f"/api/v1/transactions/ethereum/{tx_hash}", headers=headers)
    assert first.status_code == 200
    assert first.json()["value_wei"] == "1000000000000000000"
    assert first.json()["receipt_status"] == "success"
    assert first.json()["source"] == "rpc.example"

    current_receipt["status"] = "0x0"
    second = client.get(f"/api/v1/transactions/ethereum/{tx_hash}", headers=headers)
    assert second.status_code == 200
    assert second.json()["receipt_status"] == "reverted"
    with TestSession() as db:
        stored = db.scalar(select(EthereumTransaction).where(EthereumTransaction.tx_hash == tx_hash))
        observations = list(db.scalars(select(EthereumTransactionObservation)))
        audits = list(db.scalars(select(AuditLog).where(AuditLog.event_type == "transaction.lookup")))
        assert stored is not None
        assert stored.chain_id == 1
        assert stored.gas_used == 21_000
        assert len(observations) == 2
        assert len(audits) == 2
        assert all(f"ethereum:1:{tx_hash}" == audit.details for audit in audits)

    invalid = client.get("/api/v1/transactions/ethereum/not-a-hash", headers=headers)
    assert invalid.status_code == 422


def test_ethereum_lookup_requires_configured_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.ethereum as ethereum

    monkeypatch.setattr(ethereum.settings, "ethereum_provider_url", None)
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    response = client.get(
        f"/api/v1/transactions/ethereum/0x{'a' * 64}",
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "Ethereum lookup is not configured"


def test_ethereum_provider_fetch_uses_validated_json_rpc_envelopes(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import app.services.ethereum as ethereum

    tx_hash = "0x" + "a" * 64
    transaction = {
        "hash": tx_hash,
        "from": "0x" + "b" * 40,
        "to": None,
        "value": "0x0",
        "gas": "0x100000",
        "blockNumber": None,
        "blockHash": None,
    }
    results = {
        "eth_chainId": "0x1",
        "eth_getTransactionByHash": transaction,
        "eth_getTransactionReceipt": None,
    }
    requests = []
    rpc_response_id: int | str = 1

    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, body: dict) -> None:
            self.content = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def iter_bytes(self):
            return iter((self.content,))

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def stream(self, method: str, url: str, json: dict, headers: dict) -> FakeResponse:
            requests.append((method, url, json))
            return FakeResponse({"jsonrpc": "2.0", "id": rpc_response_id, "result": results[json["method"]]})

    monkeypatch.setattr(ethereum.settings, "ethereum_provider_url", "https://rpc.example/api")
    monkeypatch.setattr(ethereum.httpx, "Client", FakeClient)
    payload, source = ethereum.fetch_ethereum_transaction(tx_hash)
    assert source == "rpc.example"
    assert payload["chain_id"] == "0x1"
    assert payload["transaction"]["hash"] == tx_hash
    assert [request[2]["method"] for request in requests] == [
        "eth_chainId",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
    ]
    assert all(request[0] == "POST" and request[1] == "https://rpc.example/api" for request in requests)
    rpc_response_id = "1"
    with pytest.raises(HTTPException) as error:
        ethereum.fetch_ethereum_transaction(tx_hash)
    assert error.value.status_code == 502


def test_ethereum_address_snapshot_pins_balance_and_nonce_to_latest_block(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import app.services.ethereum as ethereum

    address = "0x" + "b" * 40
    block_hash = "0x" + "c" * 64
    results = {
        "eth_chainId": "0x1",
        "eth_getBlockByNumber": {"number": "0x1234", "hash": block_hash},
        "eth_getBalance": "0xde0b6b3a7640000",
        "eth_getTransactionCount": "0x2a",
    }
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, body: dict) -> None:
            self.content = json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def iter_bytes(self):
            return iter((self.content,))

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def stream(self, method: str, url: str, json: dict, headers: dict) -> FakeResponse:
            calls.append(json)
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": results[json["method"]]})

    monkeypatch.setattr(ethereum.settings, "ethereum_provider_url", "https://rpc.example/api")
    monkeypatch.setattr(ethereum.httpx, "Client", FakeClient)
    snapshot = ethereum.fetch_ethereum_address_snapshot(address)
    assert snapshot["chain_id"] == 1
    assert snapshot["address"] == address
    assert snapshot["balance_wei"] == "1000000000000000000"
    assert snapshot["transaction_count"] == 42
    assert snapshot["block_number"] == 0x1234
    assert snapshot["block_hash"] == block_hash
    assert snapshot["source"] == "rpc.example"
    assert calls[2]["params"] == [address, "0x1234"]
    assert calls[3]["params"] == [address, "0x1234"]
    results["eth_chainId"] = "invalid"
    with pytest.raises(HTTPException) as error:
        ethereum.fetch_ethereum_address_snapshot(address)
    assert error.value.status_code == 502


def test_ethereum_address_snapshot_api_validates_and_audits(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routes.transactions as transaction_routes

    address = "0x" + "b" * 40
    monkeypatch.setattr(
        transaction_routes,
        "fetch_ethereum_address_snapshot",
        lambda requested: {
            "chain_id": 1,
            "address": requested.lower(),
            "balance_wei": "1000",
            "transaction_count": 3,
            "block_number": 10,
            "block_hash": "0x" + "c" * 64,
            "source": "rpc.example",
            "retrieved_at": datetime.now(timezone.utc),
        },
    )
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    response = client.get(f"/api/v1/addresses/ethereum/{address}", headers=headers)
    assert response.status_code == 200
    assert response.json()["balance_wei"] == "1000"
    assert response.json()["transaction_count"] == 3
    invalid = client.get("/api/v1/addresses/ethereum/not-an-address", headers=headers)
    assert invalid.status_code == 422
    with TestSession() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "address.lookup"))
        assert audit is not None
        assert audit.details == f"ethereum:1:{address}; block=10; source=rpc.example"


def test_heuristic_risk_is_explicitly_uncalibrated() -> None:
    result = assess_bitcoin_transaction(
        {
            "fee": 1000,
            "vin": [{"txid": str(index)} for index in range(50)],
            "vout": [{"value": 10_000_000_000}],
        }
    )
    assert result.score == 55
    assert result.category == "high"
    assert len(result.explanation) == 2
    assert "proof of fraud" in result.recommendation


def test_risk_api_persists_assessment_and_creates_tenant_alert(client: TestClient) -> None:
    with TestSession.begin() as db:
        transaction = BlockchainTransaction(
            network="bitcoin",
            txid="d" * 64,
            raw_observation={
                "fee": 1000,
                "vin": [{"txid": str(index)} for index in range(50)],
                "vout": [{"value": 10_000_000_000}],
            },
            source="test-provider",
        )
        db.add(transaction)
    response = client.post(
        "/api/v1/risk/analyze",
        json={"txid": "d" * 64},
        headers={"Authorization": f"Bearer {token_for(client)}"},
    )
    assert response.status_code == 200
    assert response.json()["calibrated"] is False
    assert response.json()["score"] == 55
    alerts = client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {token_for(client)}"})
    assert alerts.status_code == 200
    assert len(alerts.json()) == 1
    assert alerts.json()[0]["severity"] == "high"


def test_alert_filters_cover_network_assignment_status_severity_and_time(client: TestClient) -> None:
    from app.models import RiskAssessment

    assigned_id = None
    with TestSession.begin() as db:
        user = db.query(User).filter_by(email="analyst@example.com").one()
        assigned_id = user.id
        for index, (severity, status, assigned, created_at) in enumerate(
            [
                ("medium", "open", user.id, datetime(2024, 1, 2, tzinfo=timezone.utc)),
                ("high", "acknowledged", None, datetime(2024, 1, 3, tzinfo=timezone.utc)),
                ("critical", "open", user.id, datetime(2024, 1, 4, tzinfo=timezone.utc)),
            ]
        ):
            transaction = BlockchainTransaction(
                network="bitcoin",
                txid=f"{index + 10:064x}",
                raw_observation={},
                source="test",
            )
            db.add(transaction)
            db.flush()
            assessment = RiskAssessment(
                organization_id=user.organization_id,
                transaction_id=transaction.id,
                score=25 * (index + 1),
                category=severity,
                model_version=f"alert-test-{index}",
                calibrated=False,
                explanation=[],
                recommendation="Review.",
            )
            db.add(assessment)
            db.flush()
            db.add(
                Alert(
                    organization_id=user.organization_id,
                    transaction_id=transaction.id,
                    risk_assessment_id=assessment.id,
                    severity=severity,
                    status=status,
                    assigned_user_id=assigned,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    response = client.get(
        "/api/v1/alerts?network=bitcoin&severity=critical&status=open"
        f"&assigned_user_id={assigned_id}&created_after=2024-01-03T00%3A00%3A00Z"
        "&created_before=2024-01-05T00%3A00%3A00Z",
        headers=headers,
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["severity"] == "critical"

    unassigned = client.get("/api/v1/alerts?unassigned=true", headers=headers)
    assert [alert["severity"] for alert in unassigned.json()] == ["high"]

    conflict = client.get(
        f"/api/v1/alerts?assigned_user_id={assigned_id}&unassigned=true",
        headers=headers,
    )
    assert conflict.status_code == 422

    naive_date = client.get(
        "/api/v1/alerts?created_after=2024-01-02T00%3A00%3A00",
        headers=headers,
    )
    assert naive_date.status_code == 422

    reversed_range = client.get(
        "/api/v1/alerts?created_after=2024-01-05T00%3A00%3A00Z"
        "&created_before=2024-01-02T00%3A00%3A00Z",
        headers=headers,
    )
    assert reversed_range.status_code == 422


def test_case_and_evidence_are_tenant_scoped_and_integrity_checked(client: TestClient, tmp_path) -> None:
    owner_token = token_for(client)
    case_response = client.post(
        "/api/v1/cases",
        json={"title": "Investigate transaction", "description": "Review provider observation"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert case_response.status_code == 201
    case_id = case_response.json()["id"]

    uploaded = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        data={"source_provenance": "Provider record captured during review"},
        files={"file": ("../provider.json", b'{"ok":true}', "application/json")},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert uploaded.status_code == 201
    evidence = uploaded.json()
    assert evidence["filename"] == "provider.json"
    assert len(evidence["sha256"]) == 64

    with TestSession.begin() as db:
        item = db.get(EvidenceItem, evidence["id"])
        stored_path = tmp_path / item.storage_key
        stored_path.write_bytes(b"tampered")
        other_org = Organization(name="Other Org")
        db.add(other_org)
        db.flush()
        db.add(User(organization_id=other_org.id, email="other@example.com", password_hash=hash_password(PASSWORD), role=Role.ANALYST.value))

    other_token = token_for(client, "other@example.com")
    assert client.get(f"/api/v1/cases/{case_id}", headers={"Authorization": f"Bearer {other_token}"}).status_code == 404
    assert client.get(
        f"/api/v1/cases/{case_id}/evidence/{evidence['id']}/download",
        headers={"Authorization": f"Bearer {owner_token}"},
    ).status_code == 409
    assert client.get("/api/v1/alerts", headers={"Authorization": f"Bearer {other_token}"}).json() == []


def test_case_graph_uses_linked_observations_and_audits_access(client: TestClient) -> None:
    from app.models import CaseTransaction

    headers = {"Authorization": f"Bearer {token_for(client)}"}
    case = client.post("/api/v1/cases", json={"title": "Graph investigation"}, headers=headers).json()
    txid = "f" * 64
    with TestSession.begin() as db:
        transaction = BlockchainTransaction(
            network="bitcoin",
            txid=txid,
            is_confirmed=True,
            raw_observation={
                "status": {"confirmed": True},
                "vin": [
                    {"prevout": {"scriptpubkey_address": "bc1qsource", "value": 700}},
                    {"prevout": {"value": 100}},
                    {"prevout": "malformed"},
                    {"prevout": {"scriptpubkey_address": "invalid\naddress", "value": 5}},
                ],
                "vout": [
                    {"scriptpubkey_address": "bc1qsource", "value": 300},
                    {"scriptpubkey_address": "bc1qdestination", "value": 400},
                    {"value": 50},
                ],
            },
            source="test-provider",
        )
        db.add(transaction)
        db.flush()
        transaction_id = transaction.id

    linked = client.post(
        f"/api/v1/cases/{case['id']}/transactions",
        json={"transaction_id": transaction_id},
        headers=headers,
    )
    assert linked.status_code == 201
    graph_response = client.get(f"/api/v1/cases/{case['id']}/graph", headers=headers)
    assert graph_response.status_code == 200
    graph = graph_response.json()
    assert graph["transaction_count"] == 1
    assert graph["truncated"] is False
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 3
    source = next(node for node in graph["nodes"] if node.get("address") == "bc1qsource")
    assert source["direction"] == "both"
    assert all("\n" not in node["label"] for node in graph["nodes"])
    transaction_node = next(node for node in graph["nodes"] if node["kind"] == "transaction")
    assert transaction_node["txid"] == txid
    assert transaction_node["source"] == "test-provider"
    assert transaction_node["observed_at"] is not None
    assert transaction_node["is_confirmed"] is True
    input_edge = next(edge for edge in graph["edges"] if edge["direction"] == "input")
    output_edge = next(edge for edge in graph["edges"] if edge["direction"] == "output" and edge["target"] == source["id"])
    assert input_edge["source"] == source["id"]
    assert input_edge["target"] == transaction_node["id"]
    assert input_edge["value_satoshis"] == 700
    assert output_edge["source"] == transaction_node["id"]
    assert output_edge["value_satoshis"] == 300
    assert "do not establish common ownership" in " ".join(graph["limitations"])
    with TestSession() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.event_type == "case.graph_viewed"))
        assert audit is not None
        assert f'"case_id": {case["id"]}' in audit.details

    with TestSession.begin() as db:
        other_org = Organization(name="Graph Other Org")
        db.add(other_org)
        db.flush()
        db.add(
            User(
                organization_id=other_org.id,
                email="graph-other@example.com",
                password_hash=hash_password(PASSWORD),
                role=Role.ANALYST.value,
            )
        )
    other_token = token_for(client, "graph-other@example.com")
    denied = client.get(
        f"/api/v1/cases/{case['id']}/graph",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied.status_code == 404


def test_case_graph_bounds_transactions_nodes_and_edges(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import CaseTransaction
    from app.routes import cases as case_routes

    monkeypatch.setattr(case_routes, "MAX_GRAPH_TRANSACTIONS", 1)
    monkeypatch.setattr(case_routes, "MAX_GRAPH_NODES", 3)
    monkeypatch.setattr(case_routes, "MAX_GRAPH_EDGES", 1)
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    case = client.post("/api/v1/cases", json={"title": "Bounded graph"}, headers=headers).json()
    with TestSession.begin() as db:
        user = db.query(User).filter_by(email="analyst@example.com").one()
        for index in range(2):
            transaction = BlockchainTransaction(
                network="bitcoin",
                txid=f"{index + 1:064x}",
                raw_observation={
                    "vin": [{"prevout": {"scriptpubkey_address": f"bc1qinput{index}"}}],
                    "vout": [{"scriptpubkey_address": f"bc1qoutput{index}"}],
                },
                source="test-provider",
            )
            db.add(transaction)
            db.flush()
            db.add(
                CaseTransaction(
                    organization_id=user.organization_id,
                    case_id=case["id"],
                    transaction_id=transaction.id,
                    linked_by_user_id=user.id,
                )
            )

    response = client.get(f"/api/v1/cases/{case['id']}/graph", headers=headers)
    assert response.status_code == 200
    graph = response.json()
    assert graph["transaction_count"] == 1
    assert len(graph["nodes"]) <= 3
    assert len(graph["edges"]) <= 1
    assert graph["truncated"] is True


def test_case_csv_report_contains_provenance_and_escapes_spreadsheet_formulas(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    created_case = client.post(
        "/api/v1/cases",
        json={"title": "=2+2", "description": "CSV export test"},
        headers=headers,
    )
    assert created_case.status_code == 201
    case_id = created_case.json()["id"]

    with TestSession.begin() as db:
        transaction = BlockchainTransaction(
            network="bitcoin",
            txid="c" * 64,
            raw_observation={"txid": "c" * 64, "status": {"confirmed": False}},
            source="test-provider",
        )
        db.add(transaction)
        db.flush()
        transaction_id = transaction.id

    linked = client.post(
        f"/api/v1/cases/{case_id}/transactions",
        json={"transaction_id": transaction_id},
        headers=headers,
    )
    assert linked.status_code == 201
    note = client.post(
        f"/api/v1/cases/{case_id}/notes",
        json={"content": '=HYPERLINK("https://example.test","open")'},
        headers=headers,
    )
    assert note.status_code == 201
    evidence = client.post(
        f"/api/v1/cases/{case_id}/evidence",
        data={"source_provenance": "Provider export captured for report."},
        files={"file": ("source.txt", b"evidence", "text/plain")},
        headers=headers,
    )
    assert evidence.status_code == 201

    response = client.get(f"/api/v1/cases/{case_id}/report.csv", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == f'attachment; filename="case-{case_id}-report.csv"'
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert {row["record_type"] for row in rows} == {
        "case",
        "transaction",
        "note",
        "evidence",
        "activity",
        "chain_of_custody",
    }
    case_row = next(row for row in rows if row["record_type"] == "case")
    assert case_row["case_title"] == "'=2+2"
    transaction_row = next(row for row in rows if row["record_type"] == "transaction")
    assert transaction_row["txid"] == "c" * 64
    assert transaction_row["source"] == "test-provider"
    assert '"confirmed": false' in transaction_row["raw_observation"]
    note_row = next(row for row in rows if row["record_type"] == "note")
    assert note_row["content"].startswith("'=")
    evidence_row = next(row for row in rows if row["record_type"] == "evidence")
    assert evidence_row["sha256"] == evidence.json()["sha256"]
    assert evidence_row["source_provenance"] == "Provider export captured for report."
    assert any(row["record_type"] == "chain_of_custody" and row["action"] == "uploaded" for row in rows)
    with TestSession() as db:
        export_audit = db.scalar(
            select(AuditLog).where(
                AuditLog.event_type == "case.report_exported",
                AuditLog.details == f"case_id={case_id}; format=csv",
            )
        )
        assert export_audit is not None

    with TestSession.begin() as db:
        other_org = Organization(name="CSV Other Org")
        db.add(other_org)
        db.flush()
        db.add(
            User(
                organization_id=other_org.id,
                email="csv-other@example.com",
                password_hash=hash_password(PASSWORD),
                role=Role.ANALYST.value,
            )
        )
    other_token = token_for(client, "csv-other@example.com")
    denied = client.get(
        f"/api/v1/cases/{case_id}/report.csv",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert denied.status_code == 404


def test_unassigned_alert_update_rejects_cross_tenant_user(client: TestClient) -> None:
    with TestSession.begin() as db:
        transaction = BlockchainTransaction(network="bitcoin", txid="e" * 64, raw_observation={}, source="test")
        db.add(transaction)
        db.flush()
        from app.models import RiskAssessment

        risk = RiskAssessment(
            organization_id=1,
            transaction_id=transaction.id,
            score=25,
            category="medium",
            model_version="test",
            calibrated=False,
            explanation=[],
            recommendation="Review.",
        )
        db.add(risk)
        db.flush()
        alert = Alert(organization_id=1, transaction_id=transaction.id, risk_assessment_id=risk.id, severity="medium")
        db.add(alert)
        other_org = Organization(name="Second Org")
        db.add(other_org)
        db.flush()
        other_user = User(organization_id=other_org.id, email="second@example.com", password_hash=hash_password(PASSWORD), role=Role.ANALYST.value)
        db.add(other_user)
        db.flush()
        alert_id = alert.id
        other_user_id = other_user.id
    response = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"assigned_user_id": other_user_id},
        headers={"Authorization": f"Bearer {token_for(client)}"},
    )
    assert response.status_code == 422


def test_admin_invitation_is_one_time_and_role_protected(client: TestClient) -> None:
    with TestSession.begin() as db:
        user = db.query(User).filter_by(email="analyst@example.com").one()
        user.role = Role.ADMINISTRATOR.value
    administrator_token = token_for(client)
    invitation = client.post(
        "/api/v1/admin/invitations",
        json={"email": "new-investigator@example.com", "role": "investigator"},
        headers={"Authorization": f"Bearer {administrator_token}"},
    )
    assert invitation.status_code == 201
    invitation_token = invitation.json()["invitation_token"]
    accepted = client.post(
        "/api/v1/auth/accept-invitation",
        json={"invitation_token": invitation_token, "password": PASSWORD},
    )
    assert accepted.status_code == 201
    assert accepted.json()["role"] == "investigator"
    second_attempt = client.post(
        "/api/v1/auth/accept-invitation",
        json={"invitation_token": invitation_token, "password": PASSWORD},
    )
    assert second_attempt.status_code == 400


def test_admin_api_rejects_analyst(client: TestClient) -> None:
    response = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {token_for(client)}"},
    )
    assert response.status_code == 403


def test_analyst_cannot_close_case_without_supervisory_approval(client: TestClient) -> None:
    headers = {"Authorization": f"Bearer {token_for(client)}"}
    created = client.post("/api/v1/cases", json={"title": "Review"}, headers=headers)
    response = client.patch(
        f"/api/v1/cases/{created.json()['id']}",
        json={"status": "closed"},
        headers=headers,
    )
    assert response.status_code == 403
