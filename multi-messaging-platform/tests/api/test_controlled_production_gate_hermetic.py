"""Hermetic Controlled Production gate tests — no production DB/Redis.

Uses in-process mocks only. Safe to run on the host without Docker
and without touching mmp_core_api / mmp_db / mmp_redis.
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
        id=9001,
        name="hermetic-cp",
        title="hermetic-cp",
        channel="rubika",
        platform=platform,
        status=status,
        template_text="hello",
        max_total_messages=None,
        max_contacts=None,
    )


@pytest.fixture
def enable_cp(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    yield
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "false")
    get_settings.cache_clear()


@pytest.fixture
def disable_cp(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "false")
    from core_engine.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_missing_confirm_returns_409(enable_cp):
    with pytest.raises(HTTPException) as exc:
        await start_campaign(MagicMock(), _campaign(), confirm_controlled_production=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"


@pytest.mark.asyncio
async def test_confirm_false_returns_409(enable_cp):
    with pytest.raises(HTTPException) as exc:
        await start_campaign(MagicMock(), _campaign(), confirm_controlled_production=False)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_confirm_true_passes_gate_into_mocked_start(enable_cp, monkeypatch):
    campaign = _campaign()
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=True,
            code=CAMPAIGN_READY,
            message="ok",
            blockers=[],
            warnings=[],
            execution_safety_state="READY",
            technical_ready=True,
            controlled_production_confirmation_required=False,
            to_dict=lambda: {
                "code": CAMPAIGN_READY,
                "allowed_to_start": True,
                "warnings": [],
                "execution_safety_state": "READY",
            },
        )
    )

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 1}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        result = await start_campaign(
            MagicMock(),
            campaign,
            confirm_controlled_production=True,
            trigger_bridge=True,
            request_id="req-hermetic-1",
        )
    assert result["status"] == "running"
    assert campaign.status == CampaignStatus.RUNNING.value
    assert result["accepted"] is True
    assert result["request_id"] == "req-hermetic-1"
    assert result["queue_jobs_created"] == 1
    assert result["controlled_confirmation_accepted"] is True


@pytest.mark.asyncio
async def test_confirm_true_survives_cp_preflight_advisory_block(enable_cp, monkeypatch):
    """Regression for Campaign 127: Confirm=true passed the CP gate, then
    evaluate_campaign_send_preflight still returned allowed_to_start=False with
    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED — Start must treat that as satisfied.
    """
    campaign = _campaign()
    persian = "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است."
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=False,
            code=CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
            message=persian,
            blockers=[
                {
                    "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                    "message": persian,
                    "account_id": None,
                }
            ],
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
    )

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 1}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        result = await start_campaign(
            MagicMock(),
            campaign,
            confirm_controlled_production=True,
            trigger_bridge=True,
            request_id="req-cp-advisory",
        )

    assert result["status"] == "running"
    assert campaign.status == CampaignStatus.RUNNING.value
    assert result["accepted"] is True
    assert result["controlled_confirmation_accepted"] is True
    assert result["queue_jobs_created"] == 1


@pytest.mark.asyncio
async def test_controlled_off_no_approval_gate(disable_cp, monkeypatch):
    campaign = _campaign()
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=True,
            code=CAMPAIGN_READY,
            message="ok",
            blockers=[],
            warnings=[],
            execution_safety_state="READY",
            to_dict=lambda: {
                "code": CAMPAIGN_READY,
                "allowed_to_start": True,
                "warnings": [],
                "execution_safety_state": "READY",
            },
        )
    )

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 0}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        result = await start_campaign(
            MagicMock(), campaign, confirm_controlled_production=False, trigger_bridge=True
        )
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_confirm_cannot_bypass_not_prepared(enable_cp):
    campaign = _campaign(status=CampaignStatus.DRAFT.value)
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=False,
            code="CAMPAIGN_NOT_PREPARED",
            message="not prepared",
            blockers=[{"code": "CAMPAIGN_NOT_PREPARED", "message": "x"}],
            warnings=[],
            execution_safety_state="BLOCKED",
            to_dict=lambda: {},
        )
    )
    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                MagicMock(), campaign, confirm_controlled_production=True
            )
    assert exc.value.detail["code"] == "CAMPAIGN_NOT_PREPARED"


@pytest.mark.asyncio
async def test_confirm_cannot_bypass_sender_blocker(enable_cp):
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=False,
            code="CAMPAIGN_SENDER_BLOCKED",
            message="sender blocked",
            blockers=[{"code": "CAMPAIGN_SENDER_BLOCKED", "message": "x"}],
            warnings=[],
            execution_safety_state="BLOCKED",
            to_dict=lambda: {},
        )
    )
    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                MagicMock(), _campaign(), confirm_controlled_production=True
            )
    assert exc.value.detail["code"] == "CAMPAIGN_SENDER_BLOCKED"


@pytest.mark.asyncio
async def test_confirm_cannot_bypass_capacity_blocker(enable_cp):
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=False,
            code="MIN_INTERVAL_ACTIVE",
            message="wait",
            blockers=[{"code": "MIN_INTERVAL_ACTIVE", "message": "x"}],
            warnings=[],
            execution_safety_state="BLOCKED",
            to_dict=lambda: {},
        )
    )
    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                MagicMock(), _campaign(), confirm_controlled_production=True
            )
    assert exc.value.detail["code"] == "MIN_INTERVAL_ACTIVE"


@pytest.mark.asyncio
async def test_confirm_cannot_bypass_invalid_recipient(enable_cp):
    preflight = AsyncMock(
        return_value=SimpleNamespace(
            allowed_to_start=False,
            code="INVALID_RECIPIENT_PHONE",
            message="bad phone",
            blockers=[{"code": "INVALID_RECIPIENT_PHONE", "message": "x"}],
            warnings=[],
            execution_safety_state="BLOCKED",
            to_dict=lambda: {},
        )
    )
    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                MagicMock(), _campaign(), confirm_controlled_production=True
            )
    assert exc.value.detail["code"] == "INVALID_RECIPIENT_PHONE"


@pytest.mark.asyncio
async def test_missing_confirm_never_reaches_queue_bridge(enable_cp):
    bridge = AsyncMock(return_value={"pushed": 0})
    with patch(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        bridge,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException):
            await start_campaign(
                MagicMock(), _campaign(), confirm_controlled_production=False
            )
    bridge.assert_not_called()


@pytest.mark.asyncio
async def test_bale_unaffected_by_controlled_gate(enable_cp, monkeypatch):
    campaign = _campaign(platform=PlatformType.BALE)
    campaign.channel = "bale"

    async def _clear(_cid: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 0}

    async def _ping() -> bool:
        return True

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )
    monkeypatch.setattr("core_engine.services.campaign_control.ping_redis", _ping)
    monkeypatch.setattr(
        "core_engine.services.campaign_control.get_redis_client",
        lambda: SimpleNamespace(store={}),
    )

    with patch("core_engine.services.campaign_control._auto_prepare"):
        result = await start_campaign(
            MagicMock(), campaign, confirm_controlled_production=False, trigger_bridge=True
        )
    assert result["status"] == "running"


def test_preflight_contract_fields_exist_on_result_type():
    from core_engine.services.campaign_preflight import CampaignPreflightResult

    fields = set(CampaignPreflightResult.__dataclass_fields__.keys())
    for required in (
        "technical_ready",
        "controlled_production_enabled",
        "controlled_production_confirmation_required",
        "allowed_to_start_after_confirmation",
        "controlled_production_label",
    ):
        assert required in fields


def test_preflight_parity_semantics_constant():
    assert CONTROLLED_PRODUCTION_APPROVAL_REQUIRED == "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"
    assert CAMPAIGN_READY == "CAMPAIGN_READY"
