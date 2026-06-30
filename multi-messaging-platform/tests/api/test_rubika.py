import pytest

from core_engine.models import (
    Account,
    AccountStatus,
    AuditLog,
    PlatformType,
    RubikaAccountPool,
    RubikaAllowedGroup,
    RubikaSenderSchedule,
)

AUTH_HEADERS = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def rubika_account(pg_session_factory, admin_auth):
    session = pg_session_factory()
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989123450001",
        label="Rubika API Test",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    account_id = account.id
    session.close()

    yield account_id

    session = pg_session_factory()
    session.query(RubikaAccountPool).filter(
        RubikaAccountPool.account_id == account_id
    ).delete()
    session.query(AuditLog).filter(
        AuditLog.resource_type == "account", AuditLog.resource_id == str(account_id)
    ).delete()
    session.query(Account).filter(Account.id == account_id).delete()
    session.commit()
    session.close()


def test_list_rubika_accounts_empty_ok(client, admin_auth):
    response = client.get("/rubika/accounts", headers=AUTH_HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["items"], list)
    assert data["total_count"] == len(data["items"])


def test_pool_upsert_then_appears_in_list(client, admin_auth, rubika_account):
    response = client.post(
        f"/rubika/accounts/{rubika_account}/pool",
        headers=AUTH_HEADERS,
        json={"phase": "day", "priority": 2},
    )
    assert response.status_code == 200
    assert response.json()["phase"] == "day"

    response = client.get("/rubika/accounts", headers=AUTH_HEADERS)
    items = response.json()["items"]
    matching = [i for i in items if i["account_id"] == rubika_account]
    assert len(matching) == 1
    assert matching[0]["phase"] == "day"
    assert matching[0]["priority"] == 2


def test_pool_sending_and_non_sending_phases_are_mutually_exclusive(
    client, admin_auth, rubika_account
):
    """قانون امنیتی سند: اکانت ارسال (day/night) و پایش/استاتوس (listener/status)

    باید کاملاً مجزا باشند."""
    response = client.post(
        f"/rubika/accounts/{rubika_account}/pool",
        headers=AUTH_HEADERS,
        json={"phase": "day", "priority": 1},
    )
    assert response.status_code == 200

    response = client.post(
        f"/rubika/accounts/{rubika_account}/pool",
        headers=AUTH_HEADERS,
        json={"phase": "listener", "priority": 1},
    )
    assert response.status_code == 400


def test_pool_remove_membership(client, admin_auth, rubika_account):
    client.post(
        f"/rubika/accounts/{rubika_account}/pool",
        headers=AUTH_HEADERS,
        json={"phase": "night", "priority": 1},
    )
    response = client.delete(
        f"/rubika/accounts/{rubika_account}/pool/night", headers=AUTH_HEADERS
    )
    assert response.status_code == 200

    response = client.delete(
        f"/rubika/accounts/{rubika_account}/pool/night", headers=AUTH_HEADERS
    )
    assert response.status_code == 404


def test_pool_restore_resting_account(client, admin_auth, pg_session_factory, rubika_account):
    session = pg_session_factory()
    account = session.query(Account).filter(Account.id == rubika_account).first()
    account.status = AccountStatus.RESTING
    session.commit()
    session.close()

    response = client.post(
        f"/rubika/accounts/{rubika_account}/pool/restore", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert response.json()["account_status"] == "active"


@pytest.fixture
def rubika_group(pg_session_factory, admin_auth):
    session = pg_session_factory()
    session.query(RubikaAllowedGroup).filter(
        RubikaAllowedGroup.group_guid == "g_test_001"
    ).delete()
    session.commit()
    session.close()
    yield "g_test_001"
    session = pg_session_factory()
    session.query(RubikaAllowedGroup).filter(
        RubikaAllowedGroup.group_guid == "g_test_001"
    ).delete()
    session.commit()
    session.close()


def test_group_create_list_update_delete(client, admin_auth, rubika_group):
    response = client.post(
        "/rubika/groups",
        headers=AUTH_HEADERS,
        json={
            "group_guid": rubika_group,
            "group_name": "گروه فروش",
            "keywords": ["قیمت", "موجودی"],
            "red_keywords": ["کنسل"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    group_id = body["id"]
    assert body["keywords"] == ["قیمت", "موجودی"]

    response = client.post(
        "/rubika/groups", headers=AUTH_HEADERS, json={"group_guid": rubika_group}
    )
    assert response.status_code == 409

    response = client.get("/rubika/groups", headers=AUTH_HEADERS)
    assert any(g["group_guid"] == rubika_group for g in response.json()["items"])

    response = client.put(
        f"/rubika/groups/{group_id}",
        headers=AUTH_HEADERS,
        json={"is_active": False, "conversation_mode_enabled": True},
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["conversation_mode_enabled"] is True

    response = client.get(f"/rubika/groups/{group_id}/messages", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["items"] == []

    response = client.delete(f"/rubika/groups/{group_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200

    response = client.delete(f"/rubika/groups/{group_id}", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_schedule_get_and_update(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    session.query(RubikaSenderSchedule).filter(
        RubikaSenderSchedule.phase == "day"
    ).delete()
    session.commit()
    session.close()

    response = client.put(
        "/rubika/schedule/day",
        headers=AUTH_HEADERS,
        json={"start_hour": 8, "end_hour": 22, "max_per_hour": 60, "is_active": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "phase": "day",
        "start_hour": 8,
        "end_hour": 22,
        "max_per_hour": 60,
        "is_active": True,
    }

    response = client.get("/rubika/schedule", headers=AUTH_HEADERS)
    assert response.status_code == 200
    phases = {item["phase"]: item for item in response.json()["items"]}
    assert phases["day"]["max_per_hour"] == 60

    session = pg_session_factory()
    session.query(RubikaSenderSchedule).filter(
        RubikaSenderSchedule.phase == "day"
    ).delete()
    session.commit()
    session.close()
