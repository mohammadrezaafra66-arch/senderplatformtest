"""API create/PATCH for optional account message limits.

Runs only against mmp_pytest_*. Refuses live databases. Does not Prepare/Start
or create messages/queue items.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
)
from tests.isolation import (
    FORBIDDEN_WRITE_DATABASES,
    REFUSING_NON_TEST_DATABASE,
    assert_pytest_database_name,
)

AUTH = {"Authorization": "Bearer fake_token"}


@pytest.fixture(autouse=True)
def _ensure_message_limit_columns(pg_engine):
    with pg_engine.begin() as conn:
        _assert_pytest_db_conn(conn)
        conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS hourly_message_limit INTEGER"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE accounts ADD COLUMN IF NOT EXISTS daily_message_limit INTEGER"
            )
        )
        conn.execute(
            text(
                """
                DO $$ BEGIN
                  ALTER TABLE accounts ADD CONSTRAINT ck_accounts_hourly_message_limit_positive
                    CHECK (hourly_message_limit IS NULL OR hourly_message_limit > 0);
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$ BEGIN
                  ALTER TABLE accounts ADD CONSTRAINT ck_accounts_daily_message_limit_positive
                    CHECK (daily_message_limit IS NULL OR daily_message_limit > 0);
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
        )


def _assert_pytest_db_conn(conn) -> None:
    name = str(conn.execute(text("SELECT current_database()")).scalar())
    if name.lower() in {item.lower() for item in FORBIDDEN_WRITE_DATABASES}:
        raise RuntimeError(REFUSING_NON_TEST_DATABASE)
    assert_pytest_database_name(name)


def _assert_pytest_db(session) -> None:
    name = str(session.execute(text("SELECT current_database()")).scalar())
    if name.lower() in {item.lower() for item in FORBIDDEN_WRITE_DATABASES}:
        raise RuntimeError(REFUSING_NON_TEST_DATABASE)
    assert_pytest_database_name(name)


def _phone() -> str:
    return f"0912{uuid.uuid4().int % 10_000_000:07d}"


def _artifact_counts(session) -> tuple[int, int, int, int]:
    return (
        session.query(Campaign).count(),
        session.query(Message).count(),
        session.query(RenderedMessage).count(),
        session.query(StagedQueueItem).count(),
    )


def test_create_account_null_limits_are_unlimited(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    _assert_pytest_db(session)
    before = _artifact_counts(session)
    phone = _phone()
    response = client.post(
        "/accounts",
        headers=AUTH,
        json={
            "platform": "bale",
            "account_identifier": phone,
            "label": "null-caps",
            "hourly_message_limit": None,
            "daily_message_limit": None,
        },
    )
    assert response.status_code == 201
    account_id = response.json()["account_id"]
    listed = client.get("/accounts", headers=AUTH)
    assert listed.status_code == 200
    row = next(item for item in listed.json()["items"] if item["id"] == account_id)
    assert row["hourly_message_limit"] is None
    assert row["daily_message_limit"] is None
    account = session.get(Account, account_id)
    assert account is not None
    assert account.hourly_message_limit is None
    assert account.daily_message_limit is None
    assert _artifact_counts(session) == before
    session.close()


def test_patch_set_clear_omit_and_mixed_dimensions(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    _assert_pytest_db(session)
    before = _artifact_counts(session)
    phone = _phone()
    created = client.post(
        "/accounts",
        headers=AUTH,
        json={"platform": "bale", "account_identifier": phone, "status": "active"},
    )
    account_id = created.json()["account_id"]
    original_status = session.get(Account, account_id).status

    set_hourly = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"hourly_message_limit": 10},
    )
    assert set_hourly.status_code == 200
    assert set_hourly.json()["hourly_message_limit"] == 10
    assert set_hourly.json()["daily_message_limit"] is None
    assert set_hourly.json()["status"] == original_status.value
    assert set_hourly.json()["archived_at"] is None

    mixed = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"daily_message_limit": 100},
    )
    assert mixed.status_code == 200
    assert mixed.json()["hourly_message_limit"] == 10
    assert mixed.json()["daily_message_limit"] == 100

    omit = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"label": "limits-untouched"},
    )
    assert omit.status_code == 200
    assert omit.json()["hourly_message_limit"] == 10
    assert omit.json()["daily_message_limit"] == 100
    assert omit.json()["label"] == "limits-untouched"
    assert omit.json()["status"] == original_status.value

    clear_hourly = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"hourly_message_limit": None},
    )
    assert clear_hourly.status_code == 200
    assert clear_hourly.json()["hourly_message_limit"] is None
    assert clear_hourly.json()["daily_message_limit"] == 100

    daily_only_unlimited_hourly = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"daily_message_limit": None, "hourly_message_limit": 4},
    )
    assert daily_only_unlimited_hourly.status_code == 200
    assert daily_only_unlimited_hourly.json()["hourly_message_limit"] == 4
    assert daily_only_unlimited_hourly.json()["daily_message_limit"] is None

    both_unlimited = client.patch(
        f"/accounts/{account_id}",
        headers=AUTH,
        json={"hourly_message_limit": None, "daily_message_limit": None},
    )
    assert both_unlimited.status_code == 200
    assert both_unlimited.json()["hourly_message_limit"] is None
    assert both_unlimited.json()["daily_message_limit"] is None
    session.refresh(session.get(Account, account_id))
    account = session.get(Account, account_id)
    assert account.status == original_status
    assert account.archived_at is None
    assert _artifact_counts(session) == before
    session.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"hourly_message_limit": 0},
        {"daily_message_limit": 0},
        {"hourly_message_limit": -1},
        {"daily_message_limit": -5},
        {"hourly_message_limit": 1.5},
        {"daily_message_limit": 2.2},
        {"hourly_message_limit": "10"},
        {"daily_message_limit": "abc"},
        {"hourly_message_limit": True},
    ],
)
def test_patch_rejects_invalid_limits(client, admin_auth, pg_session_factory, payload):
    session = pg_session_factory()
    _assert_pytest_db(session)
    phone = _phone()
    created = client.post(
        "/accounts",
        headers=AUTH,
        json={"platform": "bale", "account_identifier": phone},
    )
    account_id = created.json()["account_id"]
    before = session.get(Account, account_id)
    status_before = before.status
    hourly_before = before.hourly_message_limit
    daily_before = before.daily_message_limit
    response = client.patch(f"/accounts/{account_id}", headers=AUTH, json=payload)
    assert response.status_code == 422
    session.expire_all()
    after = session.get(Account, account_id)
    assert after.status == status_before
    assert after.hourly_message_limit == hourly_before
    assert after.daily_message_limit == daily_before
    session.close()


def test_create_rejects_zero_limit(client, admin_auth, pg_session_factory):
    session = pg_session_factory()
    _assert_pytest_db(session)
    response = client.post(
        "/accounts",
        headers=AUTH,
        json={
            "platform": "bale",
            "account_identifier": _phone(),
            "hourly_message_limit": 0,
        },
    )
    assert response.status_code == 422
    session.close()


def test_db_check_rejects_zero_and_negative(pg_session_factory):
    session = pg_session_factory()
    _assert_pytest_db(session)
    account = Account(
        platform=PlatformType.BALE,
        status=AccountStatus.ACTIVE,
        phone_number=_phone(),
        hourly_message_limit=0,
    )
    session.add(account)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    account = Account(
        platform=PlatformType.BALE,
        status=AccountStatus.ACTIVE,
        phone_number=_phone(),
        daily_message_limit=-3,
    )
    session.add(account)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.close()
