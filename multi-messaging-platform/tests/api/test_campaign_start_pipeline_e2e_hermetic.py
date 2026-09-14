"""Isolated full Start E2E + failure matrix (hermetic, no production I/O).

Proves Confirm → gate → CP-advisory-satisfied preflight → RUNNING → queue bridge
using fakes for Redis pause/queue/provider. Does not send external messages.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core_engine.models import CampaignStatus, PlatformType
from core_engine.services.campaign_control import start_campaign
from core_engine.services.campaign_preflight import (
    CAMPAIGN_READY,
    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
)


def _campaign(*, status: str = CampaignStatus.PREPARED.value, platform=PlatformType.RUBIKA):
    return SimpleNamespace(
        id=7001,
        name="e2e-start",
        title="e2e-start",
        channel="rubika",
        platform=platform,
        status=status,
        template_text="hello",
        max_total_messages=None,
        max_contacts=None,
    )


def _cp_advisory_ready():
    return SimpleNamespace(
        allowed_to_start=False,
        code=CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
        message="cp",
        blockers=[{"code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED, "message": "cp"}],
        warnings=[],
        execution_safety_state="READY",
        technical_ready=True,
        controlled_production_confirmation_required=True,
        allowed_to_start_after_confirmation=True,
        to_dict=lambda: {
            "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
            "allowed_to_start": False,
            "warnings": [],
            "execution_safety_state": "READY",
            "technical_ready": True,
            "controlled_production_confirmation_required": True,
        },
    )


@pytest.fixture
def enable_cp(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_PRODUCTION_DEFAULT_MAX_TOTAL_MESSAGES", "5")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_isolated_full_start_e2e_confirm_queue_fake_provider(enable_cp, monkeypatch):
    campaign = _campaign()
    provider_sends = {"n": 0}

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        # Fake queue push + immediate fake worker/provider consume.
        provider_sends["n"] += 1
        return {"pushed": 1, "consumed_fake": 1, "provider_ok": True}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        AsyncMock(return_value=_cp_advisory_ready()),
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        result = await start_campaign(
            MagicMock(),
            campaign,
            confirm_controlled_production=True,
            trigger_bridge=True,
            request_id="e2e-1",
        )

    assert result["accepted"] is True
    assert result["status"] == "running"
    assert campaign.status == CampaignStatus.RUNNING.value
    assert result["queue_jobs_created"] == 1
    assert provider_sends["n"] == 1
    assert result["controlled_confirmation_accepted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,status",
    [
        ("CAMPAIGN_NOT_PREPARED", 409),
        ("NO_READY_SENDER", 409),
        ("LOGIN_REQUIRED", 409),
        ("MANUAL_REVIEW", 409),
        ("SESSION_ERROR", 409),
        ("MIN_INTERVAL_ACTIVE", 409),
        ("CAPACITY_BLOCKED", 409),
        ("CAMPAIGN_CAPACITY_UNKNOWN", 503),
        ("CAMPAIGN_DEPENDENCY_ERROR", 503),
    ],
)
async def test_start_failure_matrix_blocks_without_queue(
    enable_cp, monkeypatch, code, status
):
    campaign = _campaign()
    bridge = AsyncMock(return_value={"pushed": 1})

    async def _clear(_cid: int) -> None:
        return None

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        bridge,
    )

    preflight = SimpleNamespace(
        allowed_to_start=False,
        code=code,
        message=f"blocked:{code}",
        blockers=[{"code": code, "message": f"blocked:{code}"}],
        warnings=[],
        execution_safety_state="BLOCKED",
        technical_ready=False,
        controlled_production_confirmation_required=False,
        allowed_to_start_after_confirmation=False,
        to_dict=lambda: {"code": code, "allowed_to_start": False},
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        AsyncMock(return_value=preflight),
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                MagicMock(),
                campaign,
                confirm_controlled_production=True,
                trigger_bridge=True,
            )

    assert exc.value.status_code == status
    assert exc.value.detail["code"] == code
    assert campaign.status == CampaignStatus.PREPARED.value
    bridge.assert_not_awaited()


@pytest.mark.asyncio
async def test_double_start_does_not_require_second_confirm_when_running(
    enable_cp, monkeypatch
):
    campaign = _campaign()
    pushes = {"n": 0}

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        pushes["n"] += 1
        return {"pushed": 1 if pushes["n"] == 1 else 0}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        AsyncMock(return_value=_cp_advisory_ready()),
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        r1 = await start_campaign(
            MagicMock(), campaign, confirm_controlled_production=True, trigger_bridge=True
        )
        r2 = await start_campaign(
            MagicMock(), campaign, confirm_controlled_production=True, trigger_bridge=True
        )

    assert r1["accepted"] and r2["accepted"]
    assert pushes["n"] == 2  # bridge may run, but second push is empty
    assert r2["queue_jobs_created"] == 0
