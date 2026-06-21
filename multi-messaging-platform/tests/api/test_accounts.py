import pytest
from fastapi.testclient import TestClient

from core_engine.api.auth import get_current_user
from core_engine.main import app
from core_engine.models import Account, AccountStatus, AuditLog, ChannelSession, PlatformType

client = TestClient(app)

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def sample_account(pg_session_factory, admin_auth):
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
    session.query(ChannelSession).filter(ChannelSession.account_id == account_id).delete()
    session.query(AuditLog).filter(
        AuditLog.resource_type == "account",
        AuditLog.resource_id == str(account_id),
    ).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()


def test_list_accounts(client, admin_auth):
    response = client.get("/accounts", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["items"], list)
    assert data["total_count"] == len(data["items"])


def test_create_account(client, admin_auth, pg_session_factory):
    response = client.post(
        "/accounts",
        headers=AUTH_HEADERS,
        json={
            "platform": "bale",
            "account_identifier": "09121112233",
            "label": "Bale Main",
            "status": "active",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "created"
    assert data["account_id"] > 0

    session = pg_session_factory()
    try:
        account = session.get(Account, data["account_id"])
        assert account is not None
        assert account.phone_number == "09121112233"
        assert account.platform == PlatformType.BALE

        audit = (
            session.query(AuditLog)
            .filter(
                AuditLog.action == "create_account",
                AuditLog.resource_id == str(data["account_id"]),
            )
            .first()
        )
        assert audit is not None
        assert audit.username == "admin"
    finally:
        session.query(AuditLog).filter(AuditLog.resource_id == str(data["account_id"])).delete()
        session.query(Account).filter(Account.id == data["account_id"]).delete()
        session.commit()
        session.close()


def test_create_account_duplicate_returns_409(client, admin_auth, sample_account):
    response = client.post(
        "/accounts",
        headers=AUTH_HEADERS,
        json={
            "platform": "bale",
            "account_identifier": "09120000001",
            "label": "Duplicate",
        },
    )
    assert response.status_code == 409


def test_list_accounts_filter_by_platform(client, admin_auth, sample_account):
    response = client.get("/accounts?platform=bale", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] >= 1
    assert all(item["platform"] == "bale" for item in data["items"])


def test_update_account_status(client, admin_auth, sample_account, pg_session_factory):
    response = client.patch(
        f"/accounts/{sample_account}",
        headers=AUTH_HEADERS,
        json={"status": "resting"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "resting"

    session = pg_session_factory()
    try:
        audit = (
            session.query(AuditLog)
            .filter(
                AuditLog.action == "update_account",
                AuditLog.resource_id == str(sample_account),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None
    finally:
        session.close()


def test_test_connection_success(client, admin_auth, sample_account):
    client.post(
        f"/accounts/{sample_account}/session/register",
        headers=AUTH_HEADERS,
        json={"session_payload": "TEST_BALE_TOKEN"},
    )
    response = client.post(
        f"/accounts/{sample_account}/test-connection",
        headers=AUTH_HEADERS,
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["account_id"] == sample_account
    assert data["platform"] == "bale"


def test_test_connection_banned_account_fails(client, admin_auth, sample_account):
    client.patch(
        f"/accounts/{sample_account}",
        headers=AUTH_HEADERS,
        json={"status": "banned"},
    )
    response = client.post(
        f"/accounts/{sample_account}/test-connection",
        headers=AUTH_HEADERS,
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False


def test_operator_cannot_list_accounts(client, pg_session_factory):
    async def _fake_operator():
        return {"username": "operator", "password": "operator123", "role": "operator"}

    app.dependency_overrides[get_current_user] = _fake_operator
    try:
        response = client.get("/accounts", headers=AUTH_HEADERS)
        assert response.status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_get_account_not_found_on_update(client, admin_auth):
    response = client.patch(
        "/accounts/99999",
        headers=AUTH_HEADERS,
        json={"status": "active"},
    )
    assert response.status_code == 404
