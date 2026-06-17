import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.database import get_db
from core_engine.main import app
from core_engine.services.audit_service import record_audit
from tests.audit.fake_db import AuditFakeSession


def _auth_headers(client, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def audit_api_settings(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("SECRET_KEY", "pytest-secret-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def audit_db_override():
    session = AuditFakeSession()
    record_audit(session, "admin", "seed_action", "test", "1")

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.mark.integration
def test_admin_can_list_audit_logs(client, audit_db_override):
    headers = _auth_headers(client, "admin", "admin123")
    response = client.get("/audit/logs", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["action"] == "seed_action"


@pytest.mark.integration
def test_viewer_cannot_list_audit_logs(client, audit_db_override):
    headers = _auth_headers(client, "viewer", "viewer123")
    response = client.get("/audit/logs", headers=headers)
    assert response.status_code == 403
