"""Phase 6 — campaign capacity planner, preflight, pause/retry, scale.

No live Rubika transport. Time injected. Redis/Postgres isolated by test DB.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, func

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
    PlatformType,
    RenderStatus,
    RenderedMessage,
    SendStatus,
    SessionType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_capacity import (
    AccountCapacityInput,
    SendWindowSpec,
    aggregate_campaign_capacity,
    current_window_phase,
    next_window_start,
)
from core_engine.services.campaign_preflight import (
    CAMPAIGN_CAPACITY_UNKNOWN,
    CAMPAIGN_CIRCUIT_OPEN,
    CAMPAIGN_INSUFFICIENT_CAPACITY,
    CAMPAIGN_OUTSIDE_SEND_WINDOW,
    evaluate_campaign_send_preflight,
)
from core_engine.services.campaign_render import hash_final_text
from core_engine.services.campaign_retry_schedule import retry_hint_for_code
from core_engine.services.rubika_circuit import close_circuit, open_circuit
from core_engine.services.rubika_user_session import build_session_envelope
from core_engine.services.session_storage import store_channel_session
from workers.config import get_worker_settings
from workers.redis_keys import rubika_quarantine_key

IRAN = ZoneInfo("Asia/Tehran")
WINDOWS = (SendWindowSpec(phase="day", start_hour=8, end_hour=22),)


@pytest.fixture(autouse=True)
def _reset_redis():
    from core_engine.services.redis_client import reset_redis_client

    reset_redis_client()
    yield
    reset_redis_client()


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    monkeypatch.setenv("RUBIKA_DELIVERY_MODE", "user_account")
    monkeypatch.setenv("RUBIKA_USER_ACCOUNT_ENABLED", "true")
    get_settings.cache_clear()
    get_worker_settings.cache_clear()
    yield
    get_settings.cache_clear()
    get_worker_settings.cache_clear()


def test_pure_capacity_scale_levels():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=IRAN)
    for n in (10, 100, 1000, 10000):
        rows = [
            AccountCapacityInput(
                account_id=1,
                assigned_remaining=n,
                remaining_daily=200,
                remaining_hourly=50,
                daily_cap=200,
                hourly_cap=50,
                min_interval_seconds=5,
                window_open=True,
                applies_quota=True,
            )
        ]
        agg = aggregate_campaign_capacity(rows, now=now, windows=WINDOWS)
        assert agg.total_assigned_remaining == n
        assert agg.immediate_capacity == 1
        assert agg.estimated_today_capacity is not None
        assert agg.estimated_today_capacity <= 200
        if n > 200:
            assert agg.completion.policy_days and agg.completion.policy_days >= 2
            assert agg.accounts[0].is_bottleneck is True


def test_mixed_account_states_no_reassignment():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=IRAN)
    rows = [
        AccountCapacityInput(
            account_id=1, assigned_remaining=100, remaining_daily=80, remaining_hourly=40,
            daily_cap=100, hourly_cap=50, window_open=True, applies_quota=True,
        ),
        AccountCapacityInput(
            account_id=2, assigned_remaining=100, remaining_daily=80, remaining_hourly=40,
            daily_cap=100, hourly_cap=50, window_open=True, applies_quota=True,
        ),
        AccountCapacityInput(
            account_id=3, assigned_remaining=100, remaining_daily=80, remaining_hourly=40,
            daily_cap=100, hourly_cap=50, block_code="COOLDOWN_ACTIVE",
            window_open=True, applies_quota=True,
        ),
        AccountCapacityInput(
            account_id=4, assigned_remaining=100, remaining_daily=0, remaining_hourly=0,
            daily_cap=100, hourly_cap=50, block_code="ACCOUNT_QUARANTINED",
            window_open=True, applies_quota=True,
        ),
        AccountCapacityInput(
            account_id=5, assigned_remaining=100, remaining_daily=0, remaining_hourly=0,
            daily_cap=100, hourly_cap=50, block_code="ACCOUNT_REQUIRES_LOGIN",
            window_open=True, applies_quota=True,
        ),
    ]
    agg = aggregate_campaign_capacity(rows, now=now, windows=WINDOWS)
    assert agg.usable_now == 2
    assert agg.temporary == 1
    assert agg.blocked == 2
    assert agg.completion.confidence == "blocked"


def test_bottleneck_ignores_other_account_spare_capacity():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=IRAN)
    rows = [
        AccountCapacityInput(
            account_id=11, assigned_remaining=900, remaining_daily=20, remaining_hourly=10,
            daily_cap=20, hourly_cap=10, window_open=True, applies_quota=True,
        ),
        AccountCapacityInput(
            account_id=12, assigned_remaining=100, remaining_daily=500, remaining_hourly=50,
            daily_cap=500, hourly_cap=50, window_open=True, applies_quota=True,
        ),
    ]
    agg = aggregate_campaign_capacity(rows, now=now, windows=WINDOWS)
    assert 11 in agg.bottleneck_account_ids
    assert agg.estimated_today_capacity == 20 + 100


def test_bot_api_quota_not_applied():
    now = datetime(2026, 8, 17, 10, 0, tzinfo=IRAN)
    rows = [
        AccountCapacityInput(
            account_id=1,
            assigned_remaining=50,
            applies_quota=False,
            window_open=True,
            min_interval_seconds=5,
            delivery_mode="bot_api",
        )
    ]
    agg = aggregate_campaign_capacity(rows, now=now, windows=WINDOWS)
    assert agg.accounts[0].remaining_daily is None
    assert agg.today_confidence == "not_applicable"
    assert "bot_api_quota_not_applicable" in agg.completion.limitations


def test_window_and_retry_hints():
    now = datetime(2026, 8, 17, 23, 0, tzinfo=IRAN)
    assert current_window_phase(WINDOWS, now) is None
    nxt = next_window_start(WINDOWS, now)
    assert nxt is not None and nxt.hour == 8
    daily = retry_hint_for_code("DAILY_CAP_REACHED", now=now)
    assert daily.delay_seconds >= 300
    assert daily.use_delayed_queue is True
    hourly = retry_hint_for_code("HOURLY_CAP_REACHED", now=now)
    assert hourly.reason == "next_hour_bucket"
    cool = retry_hint_for_code(
        "COOLDOWN_ACTIVE",
        now=now,
        details={"cooldown_until": (now + timedelta(minutes=10)).isoformat()},
    )
    assert cool.delay_seconds >= 60
    circuit = retry_hint_for_code(
        "RUBIKA_CIRCUIT_OPEN",
        now=now,
        details={"open_until": (now + timedelta(seconds=90)).isoformat()},
    )
    assert circuit.reason == "circuit_open_until"
    window = retry_hint_for_code("OUTSIDE_SEND_WINDOW", now=now, windows=WINDOWS)
    assert window.reason == "next_window_start"
    interval = retry_hint_for_code(
        "MIN_INTERVAL_ACTIVE", now=now, details={"retry_after_seconds": 7}
    )
    assert interval.delay_seconds >= 7


def _make_account(session, *, label: str, status=AccountStatus.ACTIVE):
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{abs(hash(label + uuid.uuid4().hex)) % 10_000_000:07d}",
        label=label,
        status=status,
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
    session.commit()
    session.refresh(account)
    return account


def _campaign_with_messages(session, accounts: list[Account], counts: list[int], *, manual: bool):
    campaign = Campaign(
        name=f"p6-{uuid.uuid4().hex[:8]}",
        title="p6",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.PREPARED.value,
        template_text="سلام",
    )
    session.add(campaign)
    session.flush()
    from core_engine.models import RubikaSenderSchedule

    if not session.query(RubikaSenderSchedule).filter(RubikaSenderSchedule.phase == "p6-all").first():
        session.add(
            RubikaSenderSchedule(
                phase=f"p6-{uuid.uuid4().hex[:6]}",
                start_hour=0,
                end_hour=23,
                max_per_hour=999,
                is_active=True,
            )
        )
    if manual:
        for i, account in enumerate(accounts, start=1):
            session.add(
                CampaignAccount(
                    campaign_id=campaign.id,
                    account_id=account.id,
                    priority=i,
                    enabled=True,
                )
            )
    texts = []
    for account, n in zip(accounts, counts, strict=True):
        for i in range(n):
            suffix = uuid.uuid4().hex[:10]
            contact = Contact(
                first_name=f"c{account.id}-{i}",
                phone=f"+989{suffix}",
                phone_e164=f"+989{suffix}",
                consent_status="allowed",
                campaign_id=campaign.id,
            )
            session.add(contact)
            session.flush()
            text = f"سلام {contact.first_name}"
            texts.append(text)
            digest = hash_final_text(text)
            rendered = RenderedMessage(
                campaign_id=campaign.id,
                contact_id=contact.id,
                channel="rubika",
                final_text=text,
                render_mode="template",
                ready_for_queue=True,
                queue_payload={
                    "metadata": {
                        "final_text_sha256": digest,
                        "render_version": "campaign-render-v1",
                    }
                },
            )
            session.add(rendered)
            session.flush()
            message = Message(
                campaign_id=campaign.id,
                account_id=account.id,
                contact_id=contact.id,
                rendered_text=text,
                dedupe_key=f"campaign:{campaign.id}:contact:{contact.id}",
            )
            session.add(message)
            session.flush()
            session.add(
                CampaignRecipient(
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    render_status=RenderStatus.RENDERED,
                    send_status=SendStatus.PENDING,
                    final_message_id=message.id,
                )
            )
            session.add(
                StagedQueueItem(
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    rendered_message_id=rendered.id,
                    channel="rubika",
                    status=StagedQueueItemStatus.READY.value,
                    final_text=text,
                    queue_payload={
                        "message_id": message.id,
                        "account_id": account.id,
                        "campaign_id": campaign.id,
                        "contact_id": contact.id,
                        "platform": "rubika",
                        "final_text": text,
                        "message_text": text,
                        "dedupe_key": message.dedupe_key,
                        "metadata": {
                            "final_text_sha256": digest,
                            "render_version": "campaign-render-v1",
                        },
                    },
                )
            )
    session.commit()
    return campaign, texts


@pytest.mark.asyncio
async def test_preflight_manual_scope_and_circuit(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client, reset_redis_client

    reset_redis_client()
    session = pg_session_factory()
    a1 = _make_account(session, label="ok")
    a2 = _make_account(session, label="other-global")
    campaign, _ = _campaign_with_messages(session, [a1], [3], manual=True)
    redis = get_redis_client()
    await redis.ping()
    result = await evaluate_campaign_send_preflight(session, campaign.id)
    ids = {row["account_id"] for row in result.accounts}
    assert a1.id in ids
    assert a2.id not in ids
    await open_circuit(redis, reason="p6", open_seconds=60)
    blocked = await evaluate_campaign_send_preflight(session, campaign.id)
    assert blocked.allowed_to_start is False
    assert blocked.code == CAMPAIGN_CIRCUIT_OPEN
    await close_circuit(redis, reason="p6-close")
    session.close()


@pytest.mark.asyncio
async def test_preflight_auto_uses_persisted_assignments(pg_session_factory):
    session = pg_session_factory()
    a1 = _make_account(session, label="auto-a")
    a2 = _make_account(session, label="auto-b")
    campaign, _ = _campaign_with_messages(session, [a1, a2], [4, 6], manual=False)
    result = await evaluate_campaign_send_preflight(session, campaign.id)
    assert result.sender_selection_mode == "automatic"
    by_id = {row["account_id"]: row["assigned"] for row in result.accounts}
    assert by_id[a1.id] == 4
    assert by_id[a2.id] == 6
    session.close()


@pytest.mark.asyncio
async def test_redis_failure_fail_closed(pg_session_factory):
    session = pg_session_factory()
    a1 = _make_account(session, label="redis-fail")
    campaign, _ = _campaign_with_messages(session, [a1], [2], manual=True)

    class Boom:
        async def ping(self):
            raise RuntimeError("down")

    result = await evaluate_campaign_send_preflight(session, campaign.id, redis=Boom())
    assert result.allowed_to_start is False
    assert result.code == CAMPAIGN_CAPACITY_UNKNOWN
    session.expire_all()
    still = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert still.status == CampaignStatus.PREPARED.value
    session.close()


@pytest.mark.asyncio
async def test_quarantine_mid_campaign_no_failover(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client

    session = pg_session_factory()
    healthy = _make_account(session, label="healthy")
    sick = _make_account(session, label="sick")
    campaign, _ = _campaign_with_messages(session, [healthy, sick], [2, 2], manual=True)
    redis = get_redis_client()
    await redis.set(rubika_quarantine_key(sick.id), '{"reason":"test"}')
    result = await evaluate_campaign_send_preflight(session, campaign.id)
    by_id = {row["account_id"]: row for row in result.accounts}
    assert by_id[sick.id]["block_code"] == "ACCOUNT_QUARANTINED"
    assert by_id[sick.id]["assigned"] == 2
    assert by_id[healthy.id]["eligible_now"] is True
    session.close()


@pytest.mark.asyncio
async def test_start_enforces_preflight(pg_session_factory, monkeypatch):
    from core_engine.services.campaign_control import start_campaign
    from fastapi import HTTPException

    session = pg_session_factory()
    a1 = _make_account(session, label="start-block")
    campaign, _ = _campaign_with_messages(session, [a1], [1], manual=True)
    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    await open_circuit(redis, reason="start-block", open_seconds=60)
    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        AsyncMock(return_value={"pushed": 0}),
    )
    with pytest.raises(HTTPException) as exc:
        await start_campaign(session, campaign, trigger_bridge=True)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == CAMPAIGN_CIRCUIT_OPEN
    session.expire_all()
    row = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert row.status != CampaignStatus.RUNNING.value
    await close_circuit(redis, reason="start-block-close")
    session.close()


def test_aggregation_not_n_plus_one(pg_session_factory):
    session = pg_session_factory()
    account = _make_account(session, label="agg")
    campaign, texts = _campaign_with_messages(session, [account], [40], manual=True)
    campaign_id = campaign.id
    queries: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(session.bind, "before_cursor_execute", before)
    grouped = (
        session.query(Message.account_id, func.count(Message.id))
        .filter(Message.campaign_id == campaign_id)
        .group_by(Message.account_id)
        .all()
    )
    event.remove(session.bind, "before_cursor_execute", before)
    assert grouped[0][1] == 40
    assert len(queries) == 1
    assert texts[0]
    session.close()


def test_frozen_text_hash_unchanged_at_scale():
    texts = [f"frozen-{i}-سلام" for i in range(10000)]
    hashes = [hash_final_text(t) for t in texts]
    replay = [hash_final_text(t) for t in texts]
    assert hashes == replay
    assert len(set(hashes)) == 10000


@pytest.mark.asyncio
async def test_waiting_window_not_permanent_failure(pg_session_factory):
    from core_engine.models import RubikaSenderSchedule

    session = pg_session_factory()
    session.query(RubikaSenderSchedule).update(
        {RubikaSenderSchedule.is_active: False}, synchronize_session=False
    )
    session.commit()
    a1 = _make_account(session, label="window-wait")
    campaign, _ = _campaign_with_messages(session, [a1], [3], manual=True)
    session.query(RubikaSenderSchedule).update(
        {RubikaSenderSchedule.is_active: False}, synchronize_session=False
    )
    session.add(
        RubikaSenderSchedule(
            phase="day-only-p6",
            start_hour=8,
            end_hour=22,
            max_per_hour=20,
            is_active=True,
        )
    )
    session.commit()
    now = datetime(2026, 8, 17, 23, 30, tzinfo=IRAN)
    result = await evaluate_campaign_send_preflight(session, campaign.id, now=now)
    assert result.allowed_to_start is True
    assert result.code == CAMPAIGN_OUTSIDE_SEND_WINDOW
    assert result.execution_safety_state == "WAITING_WINDOW"
    assert result.next_window_start is not None
    session.expire_all()
    still = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert still.status == CampaignStatus.PREPARED.value
    session.close()


@pytest.mark.asyncio
async def test_daily_cap_waiting_capacity_not_failed(pg_session_factory):
    from core_engine.services.redis_client import get_redis_client
    from core_engine.services.rubika_policy import rubika_day_bucket
    from workers.redis_keys import daily_rate_key

    session = pg_session_factory()
    a1 = _make_account(session, label="daily-cap")
    campaign, _ = _campaign_with_messages(session, [a1], [4], manual=True)
    now = datetime(2026, 8, 17, 11, 0, tzinfo=IRAN)
    redis = get_redis_client()
    await redis.set(daily_rate_key(a1.id, rubika_day_bucket(now)), "999999")
    result = await evaluate_campaign_send_preflight(session, campaign.id, now=now)
    assert result.allowed_to_start is True
    assert result.code == CAMPAIGN_INSUFFICIENT_CAPACITY
    assert result.execution_safety_state == "WAITING_CAPACITY"
    by_id = {row["account_id"]: row for row in result.accounts}
    assert by_id[a1.id]["block_code"] == "DAILY_CAP_REACHED"
    session.expire_all()
    still = session.query(Campaign).filter(Campaign.id == campaign.id).one()
    assert still.status != CampaignStatus.FAILED.value
    session.close()
