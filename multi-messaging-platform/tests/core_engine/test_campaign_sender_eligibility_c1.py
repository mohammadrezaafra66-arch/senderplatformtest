"""C1 — campaign sender eligibility + display identity regression tests."""

from __future__ import annotations

from types import SimpleNamespace

from core_engine.services.campaign_sender_eligibility import (
    CAMPAIGN_SENDER_STATUS_LABEL_FA,
    evaluate_campaign_sender_eligibility,
    filter_auto_select_eligible,
    resolve_display_identity,
    start_blocker_for_assigned_senders,
    CampaignSenderEligibility,
)


def _rt(**kwargs):
    base = dict(
        enabled=True,
        runtime_status="READY",
        runtime_status_label="آماده ارسال",
        worker_covered=True,
        dispatch_ready=True,
        dispatch_blocker=None,
        last_verified_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _account(**kwargs):
    base = dict(
        id=27,
        platform=SimpleNamespace(value="rubika"),
        phone_number="989012345678",
        label="Sender",
        status=SimpleNamespace(value="active"),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_enabled_alone_not_eligible():
    account = _account()
    rt = _rt(runtime_status="LOGIN_REQUIRED", dispatch_ready=False, worker_covered=False)
    elig = evaluate_campaign_sender_eligibility(None, account, runtime=rt)
    assert elig.enabled is True
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "LOGIN_REQUIRED"


def test_ready_eligible():
    elig = evaluate_campaign_sender_eligibility(None, _account(), runtime=_rt())
    assert elig.campaign_eligible is True
    assert elig.blocker_code is None
    assert elig.runtime_status_label == "آماده ارسال"


def test_login_required_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(), runtime=_rt(runtime_status="LOGIN_REQUIRED", dispatch_ready=False)
    )
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "LOGIN_REQUIRED"


def test_manual_review_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(id=12), runtime=_rt(runtime_status="MANUAL_REVIEW", dispatch_ready=False)
    )
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "MANUAL_REVIEW"
    assert elig.runtime_status_label == CAMPAIGN_SENDER_STATUS_LABEL_FA["MANUAL_REVIEW"]


def test_session_error_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(id=1), runtime=_rt(runtime_status="SESSION_ERROR", dispatch_ready=False)
    )
    assert elig.blocker_code == "SESSION_ERROR"


def test_authenticated_no_worker_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None,
        _account(),
        runtime=_rt(
            runtime_status="AUTHENTICATED_NO_WORKER",
            dispatch_ready=False,
            worker_covered=False,
        ),
    )
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "AUTHENTICATED_NO_WORKER"
    assert "Worker" in (elig.runtime_status_label or "")


def test_disabled_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(), runtime=_rt(enabled=False, runtime_status="DISABLED", dispatch_ready=False)
    )
    assert elig.blocker_code == "DISABLED"


def test_capacity_exhausted_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(), runtime=_rt(), capacity_block_code="DAILY_CAP_REACHED", daily_remaining=0
    )
    assert elig.campaign_eligible is False
    assert elig.blocker_code == "CAPACITY_EXHAUSTED"


def test_circuit_blocked():
    elig = evaluate_campaign_sender_eligibility(
        None, _account(), runtime=_rt(), capacity_block_code="RUBIKA_CIRCUIT_OPEN"
    )
    assert elig.blocker_code == "CIRCUIT_BLOCKED"


def test_automatic_excludes_non_ready(monkeypatch):
    ready = _account(id=27)
    blocked = _account(id=12, phone_number="98900")
    calls = {}

    def fake_batch(db, accounts, **kwargs):
        return {
            27: evaluate_campaign_sender_eligibility(None, ready, runtime=_rt()),
            12: evaluate_campaign_sender_eligibility(
                None, blocked, runtime=_rt(runtime_status="MANUAL_REVIEW", dispatch_ready=False)
            ),
        }

    monkeypatch.setattr(
        "core_engine.services.campaign_sender_eligibility.evaluate_campaign_sender_eligibility_batch",
        fake_batch,
    )
    out = filter_auto_select_eligible(None, [ready, blocked])  # type: ignore[arg-type]
    assert [a.id for a in out] == [27]


def test_display_identity_prefers_phone():
    assert (
        resolve_display_identity(account_id=1, phone_number="989012345678", label="1")
        == "989012345678"
    )


def test_malformed_1_slash_1_never_identity():
    assert (
        resolve_display_identity(account_id=5, phone_number=None, label="1 / 1")
        == "اکانت #5"
    )
    assert resolve_display_identity(account_id=5, phone_number=None, label="1") == "اکانت #5"
    assert resolve_display_identity(account_id=5, phone_number=None, label="1/1") == "اکانت #5"


def test_display_identity_never_boolean():
    assert resolve_display_identity(account_id=9, phone_number=None, label="true") == "اکانت #9"


def test_start_blocker_no_ready():
    rows = [
        CampaignSenderEligibility(
            account_id=12,
            platform="rubika",
            display_identity="x",
            enabled=True,
            runtime_status="MANUAL_REVIEW",
            runtime_status_label="نیازمند بررسی",
            auth_ready=False,
            worker_ready=False,
            dispatch_ready=False,
            campaign_eligible=False,
            blocker_code="MANUAL_REVIEW",
            blocker_label="نیازمند بررسی",
        )
    ]
    code, _msg = start_blocker_for_assigned_senders(rows)
    assert code == "ASSIGNED_SENDER_MANUAL_REVIEW"


def test_manual_auto_base_parity():
    """Manual and auto share evaluate_campaign_sender_eligibility as the base predicate."""
    rt = _rt(runtime_status="LOGIN_REQUIRED", dispatch_ready=False)
    manual = evaluate_campaign_sender_eligibility(None, _account(), runtime=rt)
    auto = evaluate_campaign_sender_eligibility(None, _account(), runtime=rt)
    assert manual.campaign_eligible == auto.campaign_eligible
    assert manual.blocker_code == auto.blocker_code
