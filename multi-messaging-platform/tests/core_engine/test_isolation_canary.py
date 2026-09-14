"""Source-level isolation canary semantics — no live production DB access."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from tests.isolation import (
    PRODUCTION_DB_NAME,
    account_matches_production_fingerprint,
    assert_connected_database_is_not_production,
    assert_no_production_row_visibility,
    assert_test_database_url,
    campaign_matches_production_fingerprint,
    is_production_database_url,
)


def test_a_isolated_account_id_12_without_fingerprint_does_not_match():
    # Autoincrement collision alone must not look like production.
    assert (
        account_matches_production_fingerprint(
            account_id=12,
            platform="rubika",
            created_at=datetime(2026, 8, 29, 12, 0, 0),  # test-run day
        )
        is False
    )


def test_b_isolated_ids_12_and_79_do_not_match_without_created_on():
    assert (
        account_matches_production_fingerprint(
            account_id=12,
            platform="rubika",
            created_at=datetime(2026, 8, 29, 10, 0, 0),
        )
        is False
    )
    assert (
        account_matches_production_fingerprint(
            account_id=79,
            platform="rubika",
            created_at=datetime(2026, 8, 29, 10, 0, 0),
        )
        is False
    )


def test_c_actual_production_account_fingerprint_matches():
    assert (
        account_matches_production_fingerprint(
            account_id=12,
            platform="rubika",
            created_at=datetime(2026, 8, 15, 10, 50, 44, 964599),
        )
        is True
    )
    assert (
        account_matches_production_fingerprint(
            account_id=79,
            platform="PlatformType.RUBIKA",
            created_at="2026-08-15T11:30:49.867149",
        )
        is True
    )
    assert (
        campaign_matches_production_fingerprint(
            campaign_id=101,
            name="R10-CONTROLLED-REAL-SEND-2MSG",
        )
        is True
    )


def test_c_id_alone_or_wrong_name_does_not_match_campaign():
    assert (
        campaign_matches_production_fingerprint(
            campaign_id=101,
            name="p6-eb4c1474",  # isolated-style name
        )
        is False
    )
    assert (
        campaign_matches_production_fingerprint(
            campaign_id=999,
            name="R10-CONTROLLED-REAL-SEND-2MSG",
        )
        is False
    )


def test_d_production_db_name_hard_fails_in_url_helper():
    assert is_production_database_url(f"postgresql://u:p@h/{PRODUCTION_DB_NAME}") is True
    with pytest.raises(RuntimeError, match="production database"):
        assert_test_database_url(f"postgresql://u:p@h/{PRODUCTION_DB_NAME}")


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def mappings(self):
        return self

    def all(self):
        return self._value


class _FakeConn:
    def __init__(self, *, dbname: str, accounts=None, campaigns=None):
        self.dbname = dbname
        self.accounts = accounts or []
        self.campaigns = campaigns or []

    def execute(self, statement, *args, **kwargs):
        sql = str(statement)
        if "current_database" in sql:
            return _FakeResult(self.dbname)
        if "to_regclass('accounts')" in sql:
            return _FakeResult(True if self.accounts is not None else False)
        if "to_regclass('campaigns')" in sql:
            return _FakeResult(True if self.campaigns is not None else False)
        if "FROM accounts" in sql:
            return _FakeResult(self.accounts)
        if "FROM campaigns" in sql:
            return _FakeResult(self.campaigns)
        raise AssertionError(f"unexpected SQL: {sql}")


def test_a_assert_visibility_allows_test_account_id_12():
    conn = _FakeConn(
        dbname="mmp_isolated_pytest",
        accounts=[
            {
                "id": 12,
                "platform": "rubika",
                "created_at": datetime(2026, 8, 29, 14, 0, 0),
            }
        ],
        campaigns=[],
    )
    assert_no_production_row_visibility(conn)  # must not raise


def test_b_assert_visibility_allows_test_ids_12_and_79():
    conn = _FakeConn(
        dbname="mmp_isolated_pytest",
        accounts=[
            {"id": 12, "platform": "rubika", "created_at": datetime(2026, 8, 29, 1, 0, 0)},
            {"id": 79, "platform": "rubika", "created_at": datetime(2026, 8, 29, 2, 0, 0)},
        ],
        campaigns=[{"id": 101, "name": "prod-guard-deadbeef"}],
    )
    assert_no_production_row_visibility(conn)


def test_c_assert_visibility_fails_on_production_fingerprint():
    conn = _FakeConn(
        dbname="mmp_isolated_pytest",
        accounts=[
            {
                "id": 12,
                "platform": "rubika",
                "created_at": datetime(2026, 8, 15, 10, 50, 44),
            }
        ],
        campaigns=[],
    )
    with pytest.raises(RuntimeError, match="production account fingerprint"):
        assert_no_production_row_visibility(conn)

    conn2 = _FakeConn(
        dbname="mmp_isolated_pytest",
        accounts=[],
        campaigns=[{"id": 101, "name": "R10-CONTROLLED-REAL-SEND-2MSG"}],
    )
    with pytest.raises(RuntimeError, match="production campaign fingerprint"):
        assert_no_production_row_visibility(conn2)


def test_d_assert_visibility_fails_on_production_db_name_regardless_of_rows():
    conn = _FakeConn(dbname=PRODUCTION_DB_NAME, accounts=[], campaigns=[])
    with pytest.raises(RuntimeError, match="production"):
        assert_connected_database_is_not_production(conn)
    with pytest.raises(RuntimeError, match="production"):
        assert_no_production_row_visibility(conn)
