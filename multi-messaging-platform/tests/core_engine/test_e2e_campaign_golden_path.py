"""E2E golden campaign path — deterministic, no real provider send."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core_engine.models import AccountStatus, CampaignStatus, PlatformType
from core_engine.services.campaign_readiness_contract import (
    audit_campaign_sender_candidates,
    count_campaign_eligible,
)
from core_engine.services.campaign_sender_eligibility import (
    evaluate_campaign_sender_eligibility,
    filter_auto_select_eligible,
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
        id=79,
        platform=SimpleNamespace(value="rubika"),
        phone_number="989048241903",
        label="Golden",
        status=SimpleNamespace(value="active"),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestGoldenE2ECampaignContract:
    """Contract-level golden path without DB or real dispatch."""

    def test_ready_account_campaign_eligible(self):
        elig = evaluate_campaign_sender_eligibility(None, _account(), runtime=_rt())
        assert elig.campaign_eligible is True
        assert elig.runtime_status_label == "آماده ارسال"
        assert elig.display_identity == "989048241903"

    def test_manual_review_not_eligible(self):
        elig = evaluate_campaign_sender_eligibility(
            None,
            _account(id=12),
            runtime=_rt(runtime_status="MANUAL_REVIEW", dispatch_ready=False, worker_covered=True),
        )
        assert elig.campaign_eligible is False
        assert elig.blocker_code == "MANUAL_REVIEW"

    def test_auto_select_only_ready(self, monkeypatch):
        ready = _account(id=79)
        blocked = _account(id=12, phone_number="989121234567")

        def fake_batch(db, accounts, **kwargs):
            return {
                79: evaluate_campaign_sender_eligibility(None, ready, runtime=_rt()),
                12: evaluate_campaign_sender_eligibility(
                    None,
                    blocked,
                    runtime=_rt(runtime_status="MANUAL_REVIEW", dispatch_ready=False),
                ),
            }

        monkeypatch.setattr(
            "core_engine.services.campaign_sender_eligibility.evaluate_campaign_sender_eligibility_batch",
            fake_batch,
        )
        out = filter_auto_select_eligible(None, [ready, blocked])  # type: ignore[arg-type]
        assert [a.id for a in out] == [79]

    def test_assigned_not_ready_blocks_start(self):
        rows = [
            CampaignSenderEligibility(
                account_id=12,
                platform="rubika",
                display_identity="989121234567",
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
        blocker = start_blocker_for_assigned_senders(rows)
        assert blocker is not None
        assert blocker[0] == "ASSIGNED_SENDER_MANUAL_REVIEW"

    def test_enabled_alone_never_eligible(self):
        elig = evaluate_campaign_sender_eligibility(
            None,
            _account(),
            runtime=_rt(runtime_status="LOGIN_REQUIRED", dispatch_ready=False, worker_covered=False),
        )
        assert elig.enabled is True
        assert elig.campaign_eligible is False

    def test_display_identity_never_index(self):
        from core_engine.services.campaign_sender_eligibility import resolve_display_identity

        assert resolve_display_identity(account_id=1, phone_number=None, label="1") == "اکانت #1"
        assert resolve_display_identity(account_id=1, phone_number=None, label="1/1") == "اکانت #1"


class TestCampaignStartIdempotencyContract:
    """Start path must tolerate already-running without duplicate state corruption."""

    @pytest.mark.asyncio
    async def test_already_running_returns_idempotent_message(self):
        from core_engine.services.campaign_control import start_campaign

        campaign = SimpleNamespace(
            id=103,
            status=CampaignStatus.RUNNING.value,
            platform=PlatformType.RUBIKA,
        )
        db = MagicMock()

        preflight = SimpleNamespace(
            allowed_to_start=True,
            to_dict=lambda: {"code": "CAMPAIGN_READY", "allowed_to_start": True, "warnings": []},
            code="CAMPAIGN_READY",
            message="ok",
            blockers=[],
            warnings=[],
            execution_safety_state="RUNNING",
        )

        with patch(
            "core_engine.services.campaign_control._auto_prepare",
        ), patch(
            "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
            new=AsyncMock(return_value=preflight),
        ), patch(
            "core_engine.services.campaign_control.clear_campaign_pause",
            new=AsyncMock(),
        ), patch(
            "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
            new=AsyncMock(return_value={"pushed": 0}),
        ), patch(
            "core_engine.services.campaign_production_guards.controlled_production_enabled",
            return_value=False,
        ):
            result = await start_campaign(db, campaign, trigger_bridge=True)

        assert "already running" in result["message"].lower()
        assert result["status"] == "running"


class TestNegativeE2EMatrix:
    """Negative cases must fail at correct layer with explicit reason."""

    @pytest.mark.parametrize(
        "runtime_status,blocker",
        [
            ("LOGIN_REQUIRED", "LOGIN_REQUIRED"),
            ("MANUAL_REVIEW", "MANUAL_REVIEW"),
            ("SESSION_ERROR", "SESSION_ERROR"),
            ("AUTHENTICATED_NO_WORKER", "AUTHENTICATED_NO_WORKER"),
        ],
    )
    def test_non_ready_runtime_blocked(self, runtime_status, blocker):
        elig = evaluate_campaign_sender_eligibility(
            None,
            _account(id=99),
            runtime=_rt(runtime_status=runtime_status, dispatch_ready=False, worker_covered=False),
        )
        assert elig.campaign_eligible is False
        assert elig.blocker_code == blocker

    def test_capacity_exhausted_blocked(self):
        elig = evaluate_campaign_sender_eligibility(
            None,
            _account(),
            runtime=_rt(),
            capacity_block_code="DAILY_CAP_REACHED",
            daily_remaining=0,
        )
        assert elig.campaign_eligible is False
        assert elig.blocker_code == "CAPACITY_EXHAUSTED"

    def test_manual_auto_same_predicate(self, monkeypatch):
        """MANUAL_AUTO_BASE_ELIGIBILITY_SAME — auto uses same C1 batch."""
        accounts = [_account(id=79), _account(id=12, phone_number="98900")]

        def fake_batch(db, accs, **kwargs):
            return {
                79: evaluate_campaign_sender_eligibility(None, accounts[0], runtime=_rt()),
                12: evaluate_campaign_sender_eligibility(
                    None,
                    accounts[1],
                    runtime=_rt(runtime_status="MANUAL_REVIEW", dispatch_ready=False),
                ),
            }

        monkeypatch.setattr(
            "core_engine.services.campaign_sender_eligibility.evaluate_campaign_sender_eligibility_batch",
            fake_batch,
        )
        auto_ids = {a.id for a in filter_auto_select_eligible(None, accounts)}  # type: ignore[arg-type]
        manual_eligible = {
            aid
            for aid, e in fake_batch(None, accounts).items()
            if e.campaign_eligible
        }
        assert auto_ids == manual_eligible
