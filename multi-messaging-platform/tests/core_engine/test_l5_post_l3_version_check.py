"""Regression: L5 verify_l2 vs verify_post/verify_l3 alembic_version gates.

Hermetic — mocks docker psql; never touches production mmp_db.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "_l5_production_schema_apply.py"
)
_SPEC = importlib.util.spec_from_file_location("l5_production_schema_apply", _SCRIPT)
assert _SPEC and _SPEC.loader
l5 = importlib.util.module_from_spec(_SPEC)
sys.modules["l5_production_schema_apply"] = l5
_SPEC.loader.exec_module(l5)


@pytest.fixture
def baseline() -> l5.PrecheckBaseline:
    return l5.PrecheckBaseline(
        alembic_version=l5.REVISION_L2,
        total_rubika_accounts=43,
        total_channel_session_count=15,
        account12_session_ids=[657, 728],
        account79_session_ids=[600],
        session_owners={"600": 79, "657": 12, "728": 12},
        duplicate_account_ids=[2, 12, 19, 81, 92],
    )


def _invariant_responses(*, alembic_version: str, active: str = "0") -> dict[str, str]:
    """Map substrings of SQL to canned psql stdout for post-migration invariants."""
    return {
        "SELECT version_num FROM alembic_version": alembic_version,
        "column_name='session_status'": "1",
        "SELECT COUNT(*) FROM channel_sessions;": "15",
        "session_status::text='legacy_unclassified'": "15",
        "session_status::text='active'": active,
        "WHERE account_id=12": "657\n728",
        "WHERE account_id=79": "600",
        "id IN (600,657,728)": "600:79\n657:12\n728:12",
        "HAVING COUNT(*) > 1": "2\n12\n19\n81\n92",
        "FROM accounts WHERE platform": "43",
        "to_regclass('rubika_login_challenges')": "t",
        "FROM rubika_login_challenges": "0",
        "typname='rubikaloginchallengestate'": "1",
    }


def _install_psql_mock(monkeypatch: pytest.MonkeyPatch, responses: dict[str, str]) -> None:
    def _fake(sql: str) -> str:
        for needle, value in responses.items():
            if needle in sql:
                return value
        raise AssertionError(f"unexpected SQL in test mock: {sql}")

    monkeypatch.setattr(l5, "_docker_psql", _fake)
    monkeypatch.setenv("ALLOW_PRODUCTION_SCHEMA_MIGRATION", "1")


def test_verify_l2_rejects_l3_alembic_version(monkeypatch, baseline):
    _install_psql_mock(
        monkeypatch,
        _invariant_responses(alembic_version=l5.REVISION_L3),
    )
    with pytest.raises(l5.GateFailure) as exc:
        l5.verify_l2(baseline=baseline)
    assert exc.value.failed_gate == "ALEMBIC_VERSION"
    assert exc.value.expected == l5.REVISION_L2
    assert exc.value.actual == l5.REVISION_L3


def test_verify_l3_accepts_l3_alembic_version(monkeypatch, baseline):
    _install_psql_mock(
        monkeypatch,
        _invariant_responses(alembic_version=l5.REVISION_L3),
    )
    result = l5.verify_l3(baseline=baseline)
    assert result["alembic_version"] == l5.REVISION_L3
    assert result["active"] == 0
    assert result["total_sessions"] == 15
    assert result["account12"] == [657, 728]
    assert result["account79"] == [600]


def test_verify_post_aliases_verify_l3_not_l2(monkeypatch, baseline):
    """Post-L3 success must not re-assert L2 alembic_version (wrapper bug regression)."""
    _install_psql_mock(
        monkeypatch,
        _invariant_responses(alembic_version=l5.REVISION_L3),
    )
    monkeypatch.setattr(l5, "_load_baseline", lambda: baseline)
    result = l5.verify_post()
    assert result["alembic_version"] == l5.REVISION_L3
    # Must not raise GateFailure.ALEMBIC_VERSION expecting L2.


def test_verify_l3_still_enforces_active_zero(monkeypatch, baseline):
    responses = _invariant_responses(alembic_version=l5.REVISION_L3, active="1")
    _install_psql_mock(monkeypatch, responses)
    with pytest.raises(l5.GateFailure) as exc:
        l5.verify_l3(baseline=baseline)
    assert exc.value.failed_gate == "ACTIVE_ZERO"


def test_verify_l3_still_enforces_account12_mapping(monkeypatch, baseline):
    responses = _invariant_responses(alembic_version=l5.REVISION_L3)
    responses["WHERE account_id=12"] = "600\n657"  # wrong ownership shape
    _install_psql_mock(monkeypatch, responses)
    with pytest.raises(l5.GateFailure) as exc:
        l5.verify_l3(baseline=baseline)
    assert exc.value.failed_gate == "ACCOUNT12_SESSION_IDS"


def test_main_verify_post_exits_zero_on_l3_state(monkeypatch, baseline, tmp_path):
    _install_psql_mock(
        monkeypatch,
        _invariant_responses(alembic_version=l5.REVISION_L3),
    )
    monkeypatch.setattr(l5, "_load_baseline", lambda: baseline)
    monkeypatch.setattr(l5, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(l5, "ARTIFACT", tmp_path / "exec.json")
    monkeypatch.setenv("L5_APPLY_STEP", "verify_post")
    assert l5.main() == 0
