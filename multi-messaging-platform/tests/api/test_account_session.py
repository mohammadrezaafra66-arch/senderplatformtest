"""API tests for unified account session wiring (Phase 8.6)."""

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import Account, AccountStatus, AuditLog, ChannelSession, PlatformType

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def telegram_account(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.TELEGRAM,
        phone_number="@phase8_bot",
        label="Phase 8 Telegram",
        status=AccountStatus.REQUIRES_LOGIN,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()
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


def test_deploy_readiness(client, admin_auth):
    response = client.get("/accounts/deploy/readiness", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["phase"] == "9.2"
    assert data["accounts_total"] >= 0
    assert len(data["worker_services"]) >= 4


def test_session_status_missing(client, admin_auth, telegram_account):
    response = client.get(
        f"/accounts/{telegram_account}/session/status",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["account_id"] == telegram_account
    assert data["session_registered"] is False
    assert data["ready_for_delivery"] is False


def test_session_register_and_test_connection(client, admin_auth, telegram_account, pg_session_factory):
    register = client.post(
        f"/accounts/{telegram_account}/session/register",
        headers=AUTH_HEADERS,
        json={"session_payload": "TELEGRAM_BOT_TOKEN_PHASE8"},
    )
    assert register.status_code == 200
    reg_data = register.json()
    assert reg_data["success"] is True
    assert reg_data["session_type"] == "api_token"

    session = pg_session_factory()
    account = session.get(Account, telegram_account)
    assert account.status == AccountStatus.ACTIVE
    session.close()

    status = client.get(
        f"/accounts/{telegram_account}/session/status",
        headers=AUTH_HEADERS,
    ).json()
    assert status["session_registered"] is True
    assert status["ready_for_delivery"] is True

    test = client.post(
        f"/accounts/{telegram_account}/test-connection",
        headers=AUTH_HEADERS,
        json={},
    )
    assert test.status_code == 200
    assert test.json()["success"] is True


def test_session_register_rejects_whatsapp_web_mode(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09128887766",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()

    response = client.post(
        f"/accounts/{account_id}/session/register",
        headers=AUTH_HEADERS,
        json={"session_payload": '{"access_token":"x","phone_number_id":"y"}'},
    )
    assert response.status_code == 400
    assert "WhatsApp Web" in response.json()["detail"]

    cleanup = pg_session_factory()
    cleanup.query(Account).filter(Account.id == account_id).delete()
    cleanup.commit()
    cleanup.close()
