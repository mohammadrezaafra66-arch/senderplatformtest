"""API tests for operational test send (Phase 9.1+)."""

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import Account, AccountStatus, AuditLog, ChannelSession, PlatformType
from workers.payloads import WorkerResult

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def bale_account_with_session(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="bale-chat-99",
        label="Ops Send Bale",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()

    client.post(
        f"/accounts/{account_id}/session/register",
        headers=AUTH_HEADERS,
        json={"session_payload": "OPS_BALE_TOKEN"},
    )

    yield account_id

    session = pg_session_factory()
    session.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    session.query(AuditLog).filter(
        AuditLog.resource_type == "account",
        AuditLog.resource_id == str(account_id),
    ).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()


def test_deploy_readiness_phase_92(client, admin_auth):
    response = client.get("/accounts/deploy/readiness", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "9.2"
    assert data["operational_send"]["ops_live_send_api_enabled"] is False


def test_operational_send_capabilities(client, admin_auth):
    response = client.get("/accounts/operational-send/capabilities", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["dry_run_default"] is True
    assert data["live_send_allowed"] is False


def test_live_send_preflight(client, admin_auth, bale_account_with_session):
    response = client.get(
        f"/accounts/{bale_account_with_session}/operational-send/preflight",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["account_id"] == bale_account_with_session
    assert data["ready_for_live_send"] is False
    assert any(item["key"] == "ops_live_send_api_enabled" for item in data["checks"])


def test_send_test_dry_run_default(client, admin_auth, bale_account_with_session):
    response = client.post(
        f"/accounts/{bale_account_with_session}/send-test",
        headers=AUTH_HEADERS,
        json={"recipient": "bale-chat-99", "message_text": "تست dry-run"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["dry_run"] is True
    assert data["status"] == "dry_run"


def test_send_test_live_blocked_without_env(client, admin_auth, bale_account_with_session):
    response = client.post(
        f"/accounts/{bale_account_with_session}/send-test",
        headers=AUTH_HEADERS,
        json={
            "recipient": "bale-chat-99",
            "message_text": "live attempt",
            "dry_run": False,
            "confirm_live_send": True,
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "OPS_LIVE_SEND_API_ENABLED" in detail or "REAL_MESSAGE_SENDING_ENABLED" in detail


def test_send_test_live_success_with_flags(client, admin_auth, bale_account_with_session, monkeypatch):
    monkeypatch.setenv("OPS_LIVE_SEND_API_ENABLED", "true")
    monkeypatch.setenv("REAL_MESSAGE_SENDING_ENABLED", "true")
    monkeypatch.setenv("CHANNEL_CONNECTORS_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    from core_engine.config import get_settings

    get_settings.cache_clear()

    async def fake_deliver(platform, payload, settings):
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="api-live-1",
            retryable=False,
        )

    monkeypatch.setattr("workers.delivery.deliver_platform_message", fake_deliver)

    response = client.post(
        f"/accounts/{bale_account_with_session}/send-test",
        headers=AUTH_HEADERS,
        json={
            "recipient": "bale-chat-99",
            "message_text": "live ok",
            "dry_run": False,
            "confirm_live_send": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["live_send"] is True
    assert data["success"] is True
    assert data["status"] == "delivered"


def test_send_test_requires_admin(client, pg_session_factory):
    from core_engine.api.auth import get_current_user

    async def _fake_viewer():
        return {"username": "viewer", "password": "x", "role": "viewer"}

    app.dependency_overrides[get_current_user] = _fake_viewer
    try:
        response = client.post(
            "/accounts/1/send-test",
            headers=AUTH_HEADERS,
            json={},
        )
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)
