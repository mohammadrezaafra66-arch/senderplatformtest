"""GET /rubika/accounts hides archived accounts from list, count, pagination.

Does not delete or mutate pool membership, sessions, login, or delivery.
Runs only against mmp_pytest_*. Refuses live databases.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from core_engine.models import Account, AccountStatus, PlatformType, RubikaAccountPool
from tests.isolation import (
    FORBIDDEN_WRITE_DATABASES,
    REFUSING_NON_TEST_DATABASE,
    assert_pytest_database_name,
)

AUTH = {"Authorization": "Bearer fake_token"}


def _assert_pytest_db(session) -> None:
    name = str(session.execute(text("SELECT current_database()")).scalar())
    if name.lower() in {item.lower() for item in FORBIDDEN_WRITE_DATABASES}:
        raise RuntimeError(REFUSING_NON_TEST_DATABASE)
    assert_pytest_database_name(name)


def _phone() -> str:
    return "9891" + uuid.uuid4().hex[:8]


def _add_rubika(session, *, archived: bool, label: str) -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=_phone(),
        label=label,
        status=AccountStatus.ACTIVE,
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    session.add(account)
    session.flush()
    session.add(RubikaAccountPool(account_id=account.id, phase="day", priority=1))
    session.commit()
    session.refresh(account)
    return account


def test_rubika_pool_list_hides_archived_keeps_active(client, pg_session_factory, admin_auth):
    session = pg_session_factory()
    _assert_pytest_db(session)
    active = _add_rubika(session, archived=False, label="pool-visible")
    archived = _add_rubika(session, archived=True, label="pool-hidden")
    active_id = int(active.id)
    archived_id = int(archived.id)
    archived_at = archived.archived_at
    session.close()

    response = client.get("/rubika/accounts", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    ids = [int(item["account_id"]) for item in body["items"]]
    assert active_id in ids
    assert archived_id not in ids
    assert body["total_count"] == len(body["items"])
    matching = [item for item in body["items"] if int(item["account_id"]) == active_id]
    assert len(matching) == 1
    assert matching[0]["label"] == "pool-visible"
    assert matching[0]["phase"] == "day"

    session = pg_session_factory()
    _assert_pytest_db(session)
    still_account = session.query(Account).filter(Account.id == archived_id).one()
    still_pool = (
        session.query(RubikaAccountPool)
        .filter(RubikaAccountPool.account_id == archived_id)
        .one()
    )
    still_active = session.query(Account).filter(Account.id == active_id).one()
    assert still_account.archived_at is not None
    assert archived_at is not None
    assert still_pool.phase == "day"
    assert still_active.archived_at is None
    session.close()


def test_rubika_pool_pagination_skips_archived(client, pg_session_factory, admin_auth):
    session = pg_session_factory()
    _assert_pytest_db(session)
    first = _add_rubika(session, archived=False, label="page-a")
    hidden = _add_rubika(session, archived=True, label="page-hidden")
    second = _add_rubika(session, archived=False, label="page-b")
    third = _add_rubika(session, archived=False, label="page-c")
    visible_ids = {int(first.id), int(second.id), int(third.id)}
    hidden_id = int(hidden.id)
    session.close()

    full = client.get("/rubika/accounts", headers=AUTH).json()
    ids = [int(item["account_id"]) for item in full["items"]]
    assert hidden_id not in ids
    assert visible_ids.issubset(set(ids))
    assert full["total_count"] == len(full["items"])
    assert full["total_count"] >= 3

    page = client.get("/rubika/accounts?limit=1&offset=1", headers=AUTH).json()
    assert page["total_count"] == full["total_count"]
    assert len(page["items"]) == 1
    assert int(page["items"][0]["account_id"]) == ids[1]
    assert int(page["items"][0]["account_id"]) != hidden_id
