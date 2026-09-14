"""L18 API — accounts list exposes runtime truth; session status hides secrets."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core_engine.main import app
from core_engine.models import Account, AccountStatus, PlatformType

client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def sample_account(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.TELEGRAM,
        phone_number="@l18_truth_bot",
        label="L18 Truth",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    aid = account.id
    session.close()
    yield aid
    session = pg_session_factory()
    session.query(Account).filter(Account.id == aid).delete()
    session.commit()
    session.close()


def test_list_accounts_includes_runtime(client, admin_auth, sample_account):
    with patch(
        "core_engine.services.account_runtime_status.batch_worker_coverage",
        return_value={sample_account: False},
    ):
        resp = client.get("/accounts", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    items = resp.json()["items"]
    row = next(i for i in items if i["id"] == sample_account)
    assert row["status"] == "active"
    assert row["runtime_status"] is not None
    assert row["runtime_status"] != "active"
    assert row["runtime"]["runtime_status"] == row["runtime_status"]
    assert row["account_enabled"] is True
    # enabled/active must not be shown as connected-equivalent READY without credentials
    assert row["runtime_status"] == "LOGIN_REQUIRED"


def test_session_status_never_exposes_secret(client, admin_auth, sample_account):
    resp = client.get(f"/accounts/{sample_account}/session/status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    blob = str(body).lower()
    assert "ciphertext" not in blob
    assert "bot_token" not in blob
    assert "session_payload" not in blob
    assert "private_key" not in blob
    assert body.get("runtime_status") is not None


def test_test_connection_returns_reason(client, admin_auth, sample_account):
    with patch(
        "core_engine.services.account_runtime_status.batch_worker_coverage",
        return_value={sample_account: False},
    ):
        resp = client.post(
            f"/accounts/{sample_account}/test-connection",
            headers=AUTH_HEADERS,
            json={},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data.get("reason_code")
    assert data.get("runtime_status")
    assert data.get("verified_at")
