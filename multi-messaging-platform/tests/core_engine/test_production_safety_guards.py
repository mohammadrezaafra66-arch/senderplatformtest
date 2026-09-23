"""Post-R10 production safety gates — hermetic, no live Rubika send."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    MessageAttempt,
    MessageAttemptStatus,
    PlatformType,
    RenderStatus,
    RubikaAccountPool,
    SendStatus,
    SessionType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.schemas.phase4 import PrepareMessagesRequest
from core_engine.services.campaign_capacity import SendWindowSpec
from core_engine.services.campaign_preflight import (
    CAMPAIGN_READY,
    CAMPAIGN_SEND_LIMIT_REACHED,
    INVALID_MESSAGE_ASSIGNMENT,
    INVALID_RECIPIENT_PHONE,
    UNRESOLVED_TEMPLATE_PLACEHOLDER,
    evaluate_campaign_send_preflight,
)
from core_engine.services.campaign_production_guards import (
    MESSAGE_ALREADY_SENT,
    find_unresolved_placeholders,
    is_valid_iran_mobile_e164,
    message_has_terminal_success,
)
from core_engine.services.phase4_prepare import prepare_campaign_messages
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.redis_client import get_redis_client, reset_redis_client
from core_engine.services.rubika_circuit import close_circuit
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import get_worker_settings
from workers.pool_health import publish_account_coverage
from workers.redis_keys import queue_key

IRAN = ZoneInfo("Asia/Tehran")


async def _publish_coverage(redis, account_id: int) -> None:
    await publish_account_coverage(
        redis,
        platform="rubika",
        account_ids=[int(account_id)],
        hostname="prod-guard-test",
        ttl_seconds=60,
    )


@pytest.fixture(autouse=True)
def _reset_settings(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    monkeypatch.setenv("CONTROLLED_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "true")
    monkeypatch.setattr(
        "workers.rubika_account_pool.resolve_current_phase",
        lambda db, *, clock=None: "day",
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_preflight.load_send_windows",
        lambda db: [SendWindowSpec(phase="day", start_hour=8, end_hour=22)],
    )
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    reset_redis_client()
    yield
    reset_redis_client()
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_circuit_and_queues():
    """Isolate from sibling suite circuit/queue residue on shared Redis DB."""
    import asyncio

    async def _reset() -> None:
        r = get_redis_client()
        await close_circuit(r, reason="prod-guard-reset")
        await r.flushdb()

    try:
        asyncio.get_event_loop().run_until_complete(_reset())
    except Exception:
        try:
            asyncio.run(_reset())
        except Exception:
            pass
    yield
    try:
        asyncio.get_event_loop().run_until_complete(_reset())
    except Exception:
        try:
            asyncio.run(_reset())
        except Exception:
            pass


def _make_account(session, *, label: str = "a") -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(label + uuid.uuid4().hex)) % 10_000_000:07d}",
        label=label,
        status=AccountStatus.ACTIVE,
        warming_started_at=datetime.now(IRAN) - timedelta(days=20),
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
    session.add(
        RubikaAccountPool(account_id=account.id, phase="day", priority=1)
    )
    session.commit()
    return account


def _contact(session, *, valid: bool = True, first_name: str = "T") -> Contact:
    """Create a unique contact; valid=True → IR E.164 98+10 digits."""
    if valid:
        digits = f"98{abs(hash(uuid.uuid4().hex)) % 10_000_000_000:010d}"
    else:
        # Invalid: 98 + 9 digits (total 11) — not 98+10.
        digits = f"98{abs(hash(uuid.uuid4().hex)) % 1_000_000_000:09d}"
    phone = f"+{digits}"
    c = Contact(
        phone=phone,
        phone_e164=phone,
        first_name=first_name,
        consent_status="allowed",
        blacklisted=False,
    )
    session.add(c)
    session.flush()
    return c


def _campaign(
    session,
    account: Account,
    contacts: list[Contact],
    *,
    template: str,
    max_contacts: int | None = None,
) -> Campaign:
    camp = Campaign(
        name=f"prod-guard-{uuid.uuid4().hex[:8]}",
        channel="rubika",
        title="prod-guard",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.DRAFT.value,
        template_text=template,
        use_gpt=False,
        include_products=False,
        max_contacts=max_contacts,
    )
    session.add(camp)
    session.flush()
    session.add(
        CampaignAccount(
            campaign_id=camp.id, account_id=account.id, priority=1, enabled=True
        )
    )
    for c in contacts:
        session.add(
            CampaignRecipient(
                campaign_id=camp.id,
                contact_id=c.id,
                render_status=RenderStatus.PENDING,
                send_status=SendStatus.PENDING,
            )
        )
    session.commit()
    return camp


def test_iran_phone_validator_unit():
    assert is_valid_iran_mobile_e164("+989121234567").ok is True
    assert is_valid_iran_mobile_e164("989121234567").ok is True
    assert is_valid_iran_mobile_e164("09121234567").ok is False
    assert is_valid_iran_mobile_e164("+98912").ok is False
    assert is_valid_iran_mobile_e164("+98abc1234567").ok is False


def test_placeholder_detection_unit():
    assert "first_name" in find_unresolved_placeholders("سلام {{first_name}}")
    assert "first_name" in find_unresolved_placeholders("سلام {first_name}")
    assert find_unresolved_placeholders("سلام Ali") == []


@pytest.mark.asyncio
async def test_malformed_rubika_phone_blocked(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="badphone")
    bad = _contact(session, valid=False)
    camp = _campaign(session, acc, [bad], template="hello fixed")
    with pytest.raises(HTTPException) as ei:
        prepare_campaign_messages(
            session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
        )
    assert ei.value.detail["code"] == INVALID_RECIPIENT_PHONE
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    assert pf.invalid_recipient_count >= 1
    assert pf.allowed_to_start is False


@pytest.mark.asyncio
async def test_unresolved_double_brace_blocked(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="dbl")
    c = _contact(session, valid=True, first_name="Ali")
    camp = _campaign(session, acc, [c], template="سلام {{unknown_token}}")
    with pytest.raises(HTTPException) as ei:
        prepare_campaign_messages(
            session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
        )
    assert ei.value.detail["code"] == UNRESOLVED_TEMPLATE_PLACEHOLDER


@pytest.mark.asyncio
async def test_unresolved_single_brace_blocked(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="sgl")
    c = _contact(session, valid=True, first_name="Ali")
    camp = _campaign(session, acc, [c], template="سلام {first_name}")
    with pytest.raises(HTTPException) as ei:
        prepare_campaign_messages(
            session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
        )
    assert ei.value.detail["code"] == UNRESOLVED_TEMPLATE_PLACEHOLDER


@pytest.mark.asyncio
async def test_missing_worker_coverage_blocks_start(pg_session_factory, monkeypatch):
    session = pg_session_factory()
    acc = _make_account(session, label="nocov")
    c = _contact(session, valid=True)
    camp = _campaign(session, acc, [c], template="متن ثابت بدون placeholder")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )

    async def _no_cov(*_a, **_k):
        return False

    monkeypatch.setattr(
        "workers.pool_health.has_active_worker_coverage",
        _no_cov,
    )
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    assert pf.allowed_to_start is False
    assert any(row.get("block_code") == "NO_WORKER_CONSUMER" for row in pf.accounts) or any(
        b.get("code") in {"NO_WORKER_CONSUMER", "CAMPAIGN_SENDER_BLOCKED"} for b in pf.blockers
    )


@pytest.mark.asyncio
async def test_readiness_separate_from_coverage(pg_session_factory, monkeypatch):
    session = pg_session_factory()
    acc = _make_account(session, label="sep")
    c = _contact(session, valid=True)
    camp = _campaign(session, acc, [c], template="متن ثابت")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )

    async def _no_cov(*_a, **_k):
        return False

    monkeypatch.setattr("workers.pool_health.has_active_worker_coverage", _no_cov)
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    # Session readiness must remain visible even when coverage blocks send.
    assert any(row.get("account_ready_now") for row in pf.accounts) or any(
        row.get("readiness") in {"READY", "ready", "session_ready"} for row in pf.accounts
    )
    assert any(row.get("worker_coverage") is False for row in pf.accounts) or any(
        row.get("block_code") == "NO_WORKER_CONSUMER" for row in pf.accounts
    )


@pytest.mark.asyncio
async def test_wrong_account_assignment_blocked(pg_session_factory):
    session = pg_session_factory()
    a1 = _make_account(session, label="a1")
    a2 = _make_account(session, label="a2")
    c = _contact(session, valid=True)
    camp = _campaign(session, a1, [c], template="متن ثابت")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )
    msg = session.query(Message).filter(Message.campaign_id == camp.id).one()
    msg.account_id = a2.id
    session.commit()
    r = get_redis_client()
    await _publish_coverage(r, a1.id)
    await _publish_coverage(r, a2.id)
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    assert pf.allowed_to_start is False
    assert pf.code == INVALID_MESSAGE_ASSIGNMENT or any(
        b.get("code") == INVALID_MESSAGE_ASSIGNMENT for b in pf.blockers
    )


@pytest.mark.asyncio
async def test_success_message_not_redispatched(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="dedupe")
    c = _contact(session, valid=True)
    camp = _campaign(session, acc, [c], template="متن ثابت")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )
    msg = session.query(Message).filter(Message.campaign_id == camp.id).one()
    session.add(
        MessageAttempt(
            message_id=msg.id,
            attempt_no=1,
            status=MessageAttemptStatus.SUCCESS,
        )
    )
    recip = (
        session.query(CampaignRecipient)
        .filter(CampaignRecipient.campaign_id == camp.id)
        .one()
    )
    recip.send_status = SendStatus.DELIVERED
    staged = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == camp.id)
        .one()
    )
    staged.status = StagedQueueItemStatus.READY.value
    camp.status = CampaignStatus.RUNNING.value
    session.commit()
    assert message_has_terminal_success(session, msg.id) is True

    result = await push_staged_items_to_worker_queue(session, batch_size=10)
    staged2 = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == camp.id)
        .one()
    )
    assert staged2.status == StagedQueueItemStatus.SKIPPED.value
    assert staged2.skip_reason == MESSAGE_ALREADY_SENT
    assert int(result.get("pushed") or 0) == 0


@pytest.mark.asyncio
async def test_campaign_cap_five_blocks_sixth(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="cap")
    contacts = [_contact(session, valid=True, first_name=f"C{i}") for i in range(6)]
    camp = _campaign(session, acc, contacts, template="متن ثابت", max_contacts=5)
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=10, force_mock_output=False)
    )
    prepared = session.query(Message).filter(Message.campaign_id == camp.id).count()
    assert prepared <= 5
    session.add(
        Message(
            campaign_id=camp.id,
            account_id=acc.id,
            contact_id=contacts[5].id,
            rendered_text="x",
            dedupe_key=f"extra-{uuid.uuid4().hex}",
        )
    )
    session.commit()
    r = get_redis_client()
    await _publish_coverage(r, acc.id)
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    assert pf.allowed_to_start is False
    assert pf.code == CAMPAIGN_SEND_LIMIT_REACHED


@pytest.mark.asyncio
async def test_paused_campaign_cannot_dispatch(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="paused")
    c = _contact(session, valid=True)
    camp = _campaign(session, acc, [c], template="متن ثابت")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )
    camp.status = CampaignStatus.PAUSED.value
    session.commit()
    result = await push_staged_items_to_worker_queue(session, batch_size=10)
    assert int(result.get("pushed") or 0) == 0
    r = get_redis_client()
    assert int(await r.llen(queue_key("rubika", acc.id))) == 0


@pytest.mark.asyncio
async def test_valid_controlled_campaign_ready(pg_session_factory):
    session = pg_session_factory()
    acc = _make_account(session, label="ok")
    c = _contact(session, valid=True, first_name="Ok")
    camp = _campaign(session, acc, [c], template="سلام {{first_name}} — تست کنترل‌شده")
    prepare_campaign_messages(
        session, camp.id, PrepareMessagesRequest(limit=1, force_mock_output=False)
    )
    r = get_redis_client()
    await _publish_coverage(r, acc.id)
    pf = await evaluate_campaign_send_preflight(session, camp.id)
    assert pf.code == CAMPAIGN_READY
    assert pf.allowed_to_start is True
    assert pf.invalid_recipient_count == 0
    assert pf.valid_recipient_count == 1
    assert pf.prepared_messages >= 1
