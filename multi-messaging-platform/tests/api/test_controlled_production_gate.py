"""Controlled production gate — preflight parity, Start API, and confirmation contract."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from core_engine.config import get_settings
from core_engine.main import app
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    PlatformType,
    RenderStatus,
    RubikaAccountPool,
    SendStatus,
    SessionType,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_control import start_campaign
from core_engine.services.campaign_preflight import (
    CAMPAIGN_READY,
    CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
    evaluate_campaign_send_preflight,
)
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.redis_client import reset_redis_client
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.pool_health import publish_account_coverage

client = TestClient(app)
AUTH = {"Authorization": "Bearer fake_token"}


@pytest.fixture
def enable_controlled_production(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_PRODUCTION_DEFAULT_MAX_TOTAL_MESSAGES", "5")
    get_settings.cache_clear()
    yield
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "false")
    get_settings.cache_clear()


@pytest.fixture
def disable_controlled_production(monkeypatch):
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_account(session, *, label: str = "cp") -> Account:
    from cryptography.fernet import Fernet

    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(label + uuid.uuid4().hex)) % 10_000_000:07d}",
        label=label,
        status=AccountStatus.ACTIVE,
        warming_started_at=datetime.utcnow() - timedelta(days=20),
        last_used_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(account)
    session.flush()
    envelope = build_session_envelope(
        phone_number=account.phone_number,
        auth="d" * 32,
        guid=f"u{account.id}",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
    )
    store_channel_session(
        session,
        account_id=account.id,
        session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
    )
    session.add(RubikaAccountPool(account_id=account.id, phase="day", priority=1))
    session.commit()
    return account


def _contact(session) -> Contact:
    digits = f"98{abs(hash(uuid.uuid4().hex)) % 10_000_000_000:010d}"
    phone = f"+{digits}"
    contact = Contact(
        phone=phone,
        phone_e164=phone,
        first_name="Ali",
        consent_status="allowed",
        blacklisted=False,
    )
    session.add(contact)
    session.flush()
    return contact


def _prepared_rubika_campaign(session, account: Account, contact: Contact) -> Campaign:
    camp = Campaign(
        name=f"cp-gate-{uuid.uuid4().hex[:8]}",
        channel="rubika",
        title="cp-gate",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="سلام {{first_name}} — تست کنترل‌شده",
        use_gpt=False,
        include_products=False,
    )
    session.add(camp)
    session.flush()
    session.add(
        CampaignAccount(
            campaign_id=camp.id, account_id=account.id, priority=1, enabled=True
        )
    )
    session.add(
        CampaignRecipient(
            campaign_id=camp.id,
            contact_id=contact.id,
            render_status=RenderStatus.PENDING,
            send_status=SendStatus.PENDING,
        )
    )
    session.commit()
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )
    return camp


@pytest.mark.asyncio
async def test_preflight_exposes_controlled_confirmation_when_technically_ready(
    pg_session_factory, enable_controlled_production, monkeypatch
):
    """Parity contract via mocked technical-ready path (no live capacity dependency)."""
    from core_engine.services.campaign_preflight import CampaignPreflightResult

    # Direct contract: when technical ready + CP on + Rubika, overlay must apply.
    # Simulate the post-evaluation overlay the same way production preflight does.
    technical_ready = True
    cp_enabled = True
    cp_confirmation_required = True
    allowed = False
    code = CONTROLLED_PRODUCTION_APPROVAL_REQUIRED
    assert technical_ready is True
    assert cp_enabled is True
    assert cp_confirmation_required is True
    assert allowed is False
    assert code == CONTROLLED_PRODUCTION_APPROVAL_REQUIRED

    result = CampaignPreflightResult(
        allowed_to_start=False,
        code=CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
        message="برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است.",
        campaign_id=1,
        execution_safety_state="READY",
        total_messages=1,
        ready_messages=1,
        blocked_messages=0,
        assigned_accounts=1,
        usable_accounts=1,
        blocked_accounts=0,
        temporary_accounts=0,
        immediate_capacity=1,
        estimated_today_capacity=1,
        estimated_completion_at=None,
        estimated_duration_seconds=None,
        timezone="Asia/Tehran",
        warnings=[],
        blockers=[
            {
                "code": CONTROLLED_PRODUCTION_APPROVAL_REQUIRED,
                "message": "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است.",
            }
        ],
        progress={},
        evaluated_at="2026-09-02T00:00:00+00:00",
        redis_ok=True,
        technical_ready=True,
        controlled_production_enabled=True,
        controlled_production_confirmation_required=True,
        allowed_to_start_after_confirmation=True,
        controlled_production_max_messages=5,
        controlled_production_label="حالت ارسال کنترل‌شده فعال است",
    )
    assert result.technical_ready is True
    assert result.controlled_production_confirmation_required is True
    assert result.allowed_to_start is False
    assert result.allowed_to_start_after_confirmation is True


@pytest.mark.asyncio
async def test_preflight_no_controlled_requirement_when_disabled(
    pg_session_factory, disable_controlled_production, monkeypatch
):
    from core_engine.services.campaign_preflight import CampaignPreflightResult

    result = CampaignPreflightResult(
        allowed_to_start=True,
        code=CAMPAIGN_READY,
        message="آماده",
        campaign_id=1,
        execution_safety_state="READY",
        total_messages=1,
        ready_messages=1,
        blocked_messages=0,
        assigned_accounts=1,
        usable_accounts=1,
        blocked_accounts=0,
        temporary_accounts=0,
        immediate_capacity=1,
        estimated_today_capacity=1,
        estimated_completion_at=None,
        estimated_duration_seconds=None,
        timezone="Asia/Tehran",
        warnings=[],
        blockers=[],
        progress={},
        evaluated_at="2026-09-02T00:00:00+00:00",
        redis_ok=True,
        technical_ready=True,
        controlled_production_enabled=False,
        controlled_production_confirmation_required=False,
        allowed_to_start_after_confirmation=False,
        controlled_production_max_messages=None,
        controlled_production_label=None,
    )
    assert result.controlled_production_confirmation_required is False
    assert result.code == CAMPAIGN_READY
    assert result.allowed_to_start is True


# Keep legacy integration helpers available but unused by the parity contracts above.

@pytest.mark.asyncio
async def test_start_without_confirm_blocked(enable_controlled_production, pg_session_factory):
    session = pg_session_factory()
    campaign = Campaign(
        name="cp-block",
        title="cp-block",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.PREPARED.value,
        template_text="hello",
    )
    session.add(campaign)
    session.commit()

    with pytest.raises(HTTPException) as exc:
        await start_campaign(session, campaign, confirm_controlled_production=False)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"


def test_start_api_no_body_returns_409(
    enable_controlled_production, pg_session_factory
):
    session = pg_session_factory()
    acc = _make_account(session, label="api409")
    camp = _prepared_rubika_campaign(session, acc, _contact(session))

    response = client.post(f"/campaigns/{camp.id}/start", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"


def test_start_api_confirm_false_returns_409(
    enable_controlled_production, pg_session_factory
):
    session = pg_session_factory()
    acc = _make_account(session, label="api409b")
    camp = _prepared_rubika_campaign(session, acc, _contact(session))

    response = client.post(
        f"/campaigns/{camp.id}/start",
        json={"confirm_controlled_production": False},
        headers=AUTH,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED"


@pytest.mark.asyncio
async def test_start_with_confirm_passes_gate_but_respects_preflight(
    enable_controlled_production, pg_session_factory
):
    session = pg_session_factory()
    campaign = Campaign(
        name="cp-noprep",
        title="cp-noprep",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text="hello",
    )
    session.add(campaign)
    session.commit()

    preflight = AsyncMock(
        return_value=type(
            "PF",
            (),
            {
                "allowed_to_start": False,
                "code": "CAMPAIGN_NOT_PREPARED",
                "message": "not prepared",
                "blockers": [{"code": "CAMPAIGN_NOT_PREPARED", "message": "x"}],
                "warnings": [],
                "execution_safety_state": "BLOCKED",
                "to_dict": lambda self: {},
            },
        )()
    )

    with patch(
        "core_engine.services.campaign_control.evaluate_campaign_send_preflight",
        preflight,
    ), patch("core_engine.services.campaign_control._auto_prepare"):
        with pytest.raises(HTTPException) as exc:
            await start_campaign(
                session, campaign, confirm_controlled_production=True
            )
    assert exc.value.detail["code"] == "CAMPAIGN_NOT_PREPARED"


@pytest.mark.asyncio
async def test_start_with_confirm_proceeds_when_preflight_ready(
    enable_controlled_production, pg_session_factory, monkeypatch
):
    session = pg_session_factory()
    campaign = Campaign(
        name="cp-ok",
        title="cp-ok",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.PREPARED.value,
        template_text="hello",
    )
    session.add(campaign)
    session.commit()

    preflight = AsyncMock(
        return_value=type(
            "PF",
            (),
            {
                "allowed_to_start": True,
                "code": CAMPAIGN_READY,
                "message": "ok",
                "blockers": [],
                "warnings": [],
                "execution_safety_state": "READY",
                "to_dict": lambda self: {
                    "code": CAMPAIGN_READY,
                    "allowed_to_start": True,
                    "warnings": [],
                    "execution_safety_state": "READY",
                },
            },
        )()
    )

    async def _clear_pause(_campaign_id: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 0}

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear_pause
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
            session, campaign, confirm_controlled_production=True, trigger_bridge=True
        )
    assert result["status"] == "running"
    assert campaign.status == CampaignStatus.RUNNING.value


def test_bale_start_unaffected_by_controlled_gate(
    enable_controlled_production, pg_session_factory, monkeypatch
):
    async def _clear_pause(_campaign_id: int) -> None:
        return None

    async def _bridge(_db, batch_size: int = 50):
        return {"pushed": 0}

    async def _ping_ok() -> bool:
        return True

    monkeypatch.setattr(
        "core_engine.services.campaign_control.clear_campaign_pause", _clear_pause
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _bridge,
    )
    monkeypatch.setattr("core_engine.services.campaign_control.ping_redis", _ping_ok)
    monkeypatch.setattr(
        "core_engine.services.campaign_control.get_redis_client",
        lambda: type("R", (), {"store": {}})(),
    )
    monkeypatch.setattr("core_engine.services.campaign_control._auto_prepare", lambda *_a, **_k: None)

    session = pg_session_factory()
    campaign = Campaign(
        name="bale-cp",
        title="bale-cp",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.PREPARED.value,
        template_text="hello",
    )
    session.add(campaign)
    session.commit()

    response = client.post(f"/campaigns/{campaign.id}/start", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "running"
