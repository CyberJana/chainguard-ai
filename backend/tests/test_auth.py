from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.dependencies import require_roles
from app.main import app
from app.models import Base, Organization, Role, User
from app.security import hash_password

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    Base.metadata.create_all(engine)

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
        db.add(
            User(
                organization_id=organization.id,
                email="analyst@example.test",
                password_hash=hash_password("correct horse battery staple"),
                role=Role.ANALYST.value,
            )
        )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def test_login_me_and_logout_revokes_only_current_session(client: TestClient) -> None:
    first = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@example.test", "password": "correct horse battery staple"},
    )
    second = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@example.test", "password": "correct horse battery staple"},
    )
    assert first.status_code == second.status_code == 200
    token_one = first.json()["access_token"]
    token_two = second.json()["access_token"]

    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_one}"}).status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token_one}"}).status_code == 204
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_one}"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token_two}"}).status_code == 200


def test_login_rejects_bad_password_and_unknown_user(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@example.test", "password": "wrong password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"


def test_me_requires_bearer_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_health_endpoint_checks_database(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_role_dependency_rejects_insufficient_role(client: TestClient) -> None:
    with TestSession() as db:
        user = db.query(User).filter_by(email="analyst@example.test").one()
        with pytest.raises(HTTPException) as error:
            require_roles(Role.ADMINISTRATOR.value)(user)
    assert error.value.status_code == 403
