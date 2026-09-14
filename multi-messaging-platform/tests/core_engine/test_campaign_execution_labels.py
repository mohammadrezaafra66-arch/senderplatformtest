"""Regression tests for campaign execution label semantics."""

from core_engine.services.campaign_execution_labels import (
    execution_blocker_label,
    resolve_account_execution_blocker,
)


def test_min_interval_persian_label():
    label = execution_blocker_label("MIN_INTERVAL_ACTIVE")
    assert label == "در انتظار فاصله مجاز ارسال"
    assert "MIN_INTERVAL_ACTIVE" not in label


def test_min_interval_with_next_send_at():
    label = execution_blocker_label(
        "MIN_INTERVAL_ACTIVE",
        next_send_at="2026-09-01T14:05:00+00:00",
    )
    assert "در انتظار فاصله مجاز ارسال" in label
    assert "ارسال بعدی" in label


def test_campaign_not_prepared_blocker():
    code, label = resolve_account_execution_blocker(
        eligible_now=False,
        account_ready_now=True,
        block_code=None,
        assignment_materialized=False,
        campaign_prepared=False,
    )
    assert code == "CAMPAIGN_NOT_PREPARED"
    assert "آماده‌سازی" in (label or "")


def test_ready_execution_has_no_blocker():
    code, label = resolve_account_execution_blocker(
        eligible_now=True,
        account_ready_now=True,
        block_code=None,
        assignment_materialized=True,
        campaign_prepared=True,
    )
    assert code is None
    assert label is None
