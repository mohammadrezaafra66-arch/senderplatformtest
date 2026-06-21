import pytest
from fastapi.testclient import TestClient

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import Account, AccountStatus, AuditLog, ChannelSession, PlatformType

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def bale_account(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.BALE,
        phone_number="09120000001",
        label="Test Bale",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()
    yield account_id
    session = pg_session_factory()
    session.query(AuditLog).filter(
        AuditLog.resource_type == "account",
        AuditLog.resource_id == str(account_id),
    ).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()


@pytest.fixture
def whatsapp_account(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number="09129998877",
        label="WA Web Test",
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


def test_whatsapp_web_status_requires_whatsapp_platform(client, admin_auth, bale_account):
    response = client.get(
        f"/accounts/{bale_account}/whatsapp-web/status",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 400


def test_whatsapp_web_status_new_account(client, admin_auth, whatsapp_account):
    response = client.get(
        f"/accounts/{whatsapp_account}/whatsapp-web/status",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["account_id"] == whatsapp_account
    assert data["linked"] is False
    assert data["needs_qr"] is True
    assert "account-" in data["profile_dir"]


def test_whatsapp_web_register_marks_linked(client, admin_auth, whatsapp_account, pg_session_factory):
    response = client.post(
        f"/accounts/{whatsapp_account}/whatsapp-web/register",
        headers=AUTH_HEADERS,
        json={"linked": True, "phone": "09129998877"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["linked"] is True

    session = pg_session_factory()
    account = session.get(Account, whatsapp_account)
    assert account.status == AccountStatus.ACTIVE

    status = client.get(
        f"/accounts/{whatsapp_account}/whatsapp-web/status",
        headers=AUTH_HEADERS,
    ).json()
    assert status["session_registered"] is True
    assert status["linked"] is True
    session.close()


def test_whatsapp_web_pool_status(client, admin_auth, monkeypatch):
    async def fake_list(_redis_client):
        return [
            {
                "hostname": "whatsapp-worker-1",
                "pool_size": 2,
                "pool_index": 0,
                "assigned_account_ids": [2, 4],
                "updated_at": "2026-06-17T12:00:00+00:00",
            }
        ]

    monkeypatch.setattr("core_engine.api.accounts.list_whatsapp_pool_workers", fake_list)

    response = client.get("/accounts/whatsapp-web/pool-status", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["workers"][0]["hostname"] == "whatsapp-worker-1"
    assert data["workers"][0]["assigned_account_ids"] == [2, 4]
