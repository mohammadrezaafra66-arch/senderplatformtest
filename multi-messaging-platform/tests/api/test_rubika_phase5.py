"""Rubika Phase 5 — Protection Center API / RBAC tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import Account, AccountStatus, PlatformType
from core_engine.services.redis_client import reset_redis_client

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture(autouse=True)
def _reset_redis_between_tests():
    reset_redis_client()
    yield
    reset_redis_client()


def _account(session, *, label="p5api", status=AccountStatus.ACTIVE):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98916{abs(hash(label)) % 10_000_000:07d}",
        label=label,
        status=status,
        warming_started_at=datetime.now(timezone.utc) - timedelta(days=20),
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


def test_api_overview_health_incidents_restore_rbac(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    acct = _account(session, label="api_ov")
    account_id = acct.id
    session.close()

    r = client.get("/rubika/protection/overview", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "accounts" in body and "summary" in body and "alerts" in body
    assert "system" in body and "circuit" in body["system"]

    r = client.get("/rubika/health", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "circuit" in r.json()

    r = client.get("/rubika/incidents?status=OPEN&sort=newest", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "items" in r.json()

    r = client.get("/rubika/alerts", headers=AUTH_HEADERS)
    assert r.status_code == 200

    r = client.get(f"/rubika/accounts/{account_id}/health", headers=AUTH_HEADERS)
    assert r.status_code == 200

    r = client.get(f"/rubika/accounts/{account_id}/protection", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["identity"]["account_id"] == account_id

    async def _operator():
        return {"username": "operator", "password": "x", "role": "operator"}

    app.dependency_overrides[get_current_user] = _operator
    try:
        denied = client.post(f"/rubika/accounts/{account_id}/restore", headers=AUTH_HEADERS)
        assert denied.status_code in (401, 403)
    finally:
        async def _admin():
            return {"username": "admin", "password": "admin123", "role": "admin"}

        app.dependency_overrides[get_current_user] = _admin

    session = pg_session_factory()
    banned = _account(session, label="api_banned", status=AccountStatus.BANNED)
    banned_id = banned.id
    session.close()

    async def _admin2():
        return {"username": "admin", "password": "admin123", "role": "admin"}

    app.dependency_overrides[get_current_user] = _admin2
    bad = client.post(f"/rubika/accounts/{banned_id}/restore", headers=AUTH_HEADERS)
    assert bad.status_code == 400
    detail = bad.json()["detail"]
    assert detail["code"] == "BANNED"


def test_event_timeline_endpoint(client, admin_auth):
    r = client.get("/rubika/protection/events", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "items" in r.json()


def test_operator_can_read_overview(client, pg_session_factory):
    """Default api_auth_bypass is operator — read path must work."""
    r = client.get("/rubika/protection/overview", headers=AUTH_HEADERS)
    assert r.status_code == 200
    r = client.get("/rubika/alerts", headers=AUTH_HEADERS)
    assert r.status_code == 200
