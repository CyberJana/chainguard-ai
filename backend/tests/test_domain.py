from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import (
    Alert,
    Base,
    BlockchainTransaction,
    EvidenceItem,
    Organization,
    Role,
    TransactionObservation,
    User,
)
from app.config import Settings
from app.security import hash_password
from app.services.bitcoin import ingest_bitcoin_transaction, normalize_bitcoin_transaction
from app.services.risk import assess_bitcoin_transaction

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
