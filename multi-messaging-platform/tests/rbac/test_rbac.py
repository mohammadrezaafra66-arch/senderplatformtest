import pytest
from cryptography.fernet import Fernet

from core_engine.config import get_settings


def _auth_headers(client, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/auth/token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def rbac_settings(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", Fernet.generate_key().decode())
    monkeypatch.setenv("SECRET_KEY", "pytest-secret-key")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.integration
def test_admin_can_access_admin_ping(client):
    headers = _auth_headers(client, "admin", "admin123")
    response = client.get("/admin/ping", headers=headers)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.integration
def test_operator_can_access_operator_ping(client):
    headers = _auth_headers(client, "operator", "operator123")
    response = client.get("/operator/ping", headers=headers)
    assert response.status_code == 200


@pytest.mark.integration
def test_viewer_cannot_access_admin_ping(client):
    headers = _auth_headers(client, "viewer", "viewer123")
    response = client.get("/admin/ping", headers=headers)
    assert response.status_code == 403


@pytest.mark.integration
def test_viewer_cannot_set_kill_switch(client):
    headers = _auth_headers(client, "viewer", "viewer123")
    response = client.post(
        "/controls/kill-switch",
        json={"enabled": True},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.integration
def test_admin_can_set_kill_switch(monkeypatch, client):
    from core_engine.database import get_db
    from core_engine.main import app
    from tests.audit.fake_db import AuditFakeSession

    session = AuditFakeSession()

    def _override_get_db():
        yield session

    async def fake_set_kill_switch(enabled: bool):
        return {"success": True, "enabled": enabled, "redis_key": "system:kill_switch"}

    monkeypatch.setattr(
        "core_engine.api.controls.set_kill_switch",
        fake_set_kill_switch,
    )
    app.dependency_overrides[get_db] = _override_get_db
    try:
        headers = _auth_headers(client, "admin", "admin123")
        response = client.post(
            "/controls/kill-switch",
            json={"enabled": False},
            headers=headers,
        )
        assert response.status_code == 200
        assert len(session.logs) == 1
        assert session.logs[0].action == "set_kill_switch"
    finally:
        app.dependency_overrides.pop(get_db, None)
