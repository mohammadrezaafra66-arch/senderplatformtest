"""Scoped resume of one running pilot campaign. No live send and no live database."""

from __future__ import annotations

import asyncio
import json
import uuid
from threading import Barrier, Thread

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignPilotCohortMember,
    CampaignPilotState,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    Message,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_control import resume_running_campaign_queue
from core_engine.services.campaign_render import hash_final_text
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from tests.queue_bridge.test_phase6_controlled_dispatch import BridgeRedis
from workers.redis_keys import kill_switch_key, rubika_send_lease_key


@pytest.fixture
def enable_real_push(monkeypatch):
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bridge_redis(monkeypatch):
    fake = BridgeRedis()
    fake.kv[kill_switch_key()] = "true"
    monkeypatch.setattr("core_engine.services.queue_bridge.get_redis_client", lambda: fake)
    monkeypatch.setattr("core_engine.services.campaign_control.get_redis_client", lambda: fake)
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *args, **kwargs: None,
    )

    def _open_window(_db):
        return True, None

    async def _no_blocked(*args, **kwargs):
        return set()

    class _Circuit:
        state = "closed"

    async def _circuit(*args, **kwargs):
        return _Circuit()

    monkeypatch.setattr(
        "core_engine.services.campaign_send_safety.rubika_window_open",
        _open_window,
    )
    monkeypatch.setattr(
        "core_engine.services.rubika_circuit.get_circuit_snapshot",
        _circuit,
    )
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.blocked_rubika_account_ids",
        _no_blocked,
    )
    return fake


def _seed(session, *, cohort: int, extra: int = 1, other: bool = False):
    account_count = max(1, (cohort + 7) // 8)
    accounts = []
    for _ in range(account_count):
        account = Account(
            platform=PlatformType.RUBIKA,
            phone_number=f"+98{uuid.uuid4().hex[:10]}",
            label="resume",
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        accounts.append(account)
    session.flush()
    body = "متن ثابت بدون قیمت"
    footer = "footer-stable"
    template = f"{body}\n\n{footer}"
    campaign = Campaign(
        name=f"resume-{uuid.uuid4().hex[:8]}",
        title="resume",
        channel="rubika",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.RUNNING.value,
        template_text=template,
        include_products=False,
        use_gpt=False,
    )
    session.add(campaign)
    session.flush()
    session.add(
        CampaignPilotState(
            campaign_id=campaign.id,
            enabled=True,
            success_limit=100,
            recipient_limit=cohort,
            auto_pause=True,
            confirmed=0,
            reserved=0,
        )
    )
    ordinal = 0
    for index in range(cohort + extra):
        contact = Contact(
            first_name=f"n{index}",
            phone=f"+989{uuid.uuid4().hex[:10]}",
            phone_e164=f"+989{uuid.uuid4().hex[:10]}",
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        text_value = f"{template}-{contact.id}"
        digest = hash_final_text(text_value)
        rendered = RenderedMessage(
            campaign_id=campaign.id,
            contact_id=contact.id,
            channel="rubika",
            final_text=text_value,
            render_mode="template",
            ready_for_queue=True,
            used_products=False,
            queue_payload={"metadata": {"final_text_sha256": digest}},
        )
        session.add(rendered)
        session.flush()
        message = Message(
            campaign_id=campaign.id,
            account_id=accounts[index % account_count].id,
            contact_id=contact.id,
            rendered_text=text_value,
            dedupe_key=f"resume:{campaign.id}:{contact.id}",
        )
        session.add(message)
        session.flush()
        recipient = CampaignRecipient(
            campaign_id=campaign.id,
            contact_id=contact.id,
        )
        session.add(recipient)
        session.flush()
        if index < cohort:
            ordinal += 1
            session.add(
                CampaignPilotCohortMember(
                    campaign_id=campaign.id,
                    campaign_recipient_id=recipient.id,
                    contact_id=contact.id,
                    ordinal=ordinal,
                )
            )
        session.add(
            StagedQueueItem(
                campaign_id=campaign.id,
                contact_id=contact.id,
                rendered_message_id=rendered.id,
                channel="rubika",
                status=StagedQueueItemStatus.READY.value,
                final_text=text_value,
                queue_payload={
                    "message_id": message.id,
                    "account_id": accounts[index % account_count].id,
                    "campaign_id": campaign.id,
                    "contact_id": contact.id,
                    "platform": "rubika",
                    "final_text": text_value,
                    "message_text": text_value,
                    "dedupe_key": message.dedupe_key,
                    "metadata": {"final_text_sha256": digest},
                },
            )
        )
    other_id = None
    if other:
        other_campaign = Campaign(
            name=f"other-{uuid.uuid4().hex[:8]}",
            title="other",
            channel="rubika",
            platform=PlatformType.RUBIKA,
            status=CampaignStatus.RUNNING.value,
            template_text="دیگر",
            include_products=False,
        )
        session.add(other_campaign)
        session.flush()
        contact = Contact(
            first_name="other",
            phone=f"+989{uuid.uuid4().hex[:10]}",
            phone_e164=f"+989{uuid.uuid4().hex[:10]}",
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        text_value = "other-text"
        digest = hash_final_text(text_value)
        rendered = RenderedMessage(
            campaign_id=other_campaign.id,
            contact_id=contact.id,
            channel="rubika",
            final_text=text_value,
            render_mode="template",
            ready_for_queue=True,
            queue_payload={"metadata": {"final_text_sha256": digest}},
        )
        session.add(rendered)
        session.flush()
        message = Message(
            campaign_id=other_campaign.id,
            account_id=accounts[0].id,
            contact_id=contact.id,
            rendered_text=text_value,
            dedupe_key=f"other:{other_campaign.id}:{contact.id}",
        )
        session.add(message)
        session.flush()
        session.add(
            StagedQueueItem(
                campaign_id=other_campaign.id,
                contact_id=contact.id,
                rendered_message_id=rendered.id,
                channel="rubika",
                status=StagedQueueItemStatus.READY.value,
                final_text=text_value,
                queue_payload={
                    "message_id": message.id,
                    "account_id": accounts[0].id,
                    "campaign_id": other_campaign.id,
                    "contact_id": contact.id,
                    "platform": "rubika",
                    "final_text": text_value,
                    "message_text": text_value,
                    "metadata": {"final_text_sha256": digest},
                },
            )
        )
        other_id = int(other_campaign.id)
    session.commit()
    outside = (
        session.query(CampaignRecipient.id)
        .filter(CampaignRecipient.campaign_id == campaign.id)
        .order_by(CampaignRecipient.id.asc())
        .offset(cohort)
        .first()
    )
    return {
        "campaign_id": int(campaign.id),
        "template": template,
        "outside_recipient_id": None if outside is None else int(outside[0]),
        "other_campaign_id": other_id,
    }


def _queued_contacts(redis: BridgeRedis) -> list[int]:
    contacts: list[int] = []
    for _key, raw in redis.calls:
        contacts.append(int(json.loads(raw)["contact_id"]))
    return contacts


@pytest.mark.asyncio
async def test_resume_queues_only_the_cohort_and_turns_stop_off(
    pg_session_factory, enable_real_push, bridge_redis
):
    session = pg_session_factory()
    seeded = _seed(session, cohort=100, extra=1, other=True)
    before = session.get(Campaign, seeded["campaign_id"]).template_text
    result = await resume_running_campaign_queue(session, seeded["campaign_id"], actor="admin")
    assert result["accepted"] is True
    assert result["code"] == "RESUMED"
    assert result["bridge_result"]["pushed"] == 100
    assert result["emergency_stop_enabled"] is False
    assert bridge_redis.kv[kill_switch_key()] == "false"
    assert len(_queued_contacts(bridge_redis)) == 100
    outside = session.get(CampaignRecipient, seeded["outside_recipient_id"])
    outside_contact = int(outside.contact_id)
    assert outside_contact not in _queued_contacts(bridge_redis)
    outside_item = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == seeded["campaign_id"],
            StagedQueueItem.contact_id == outside_contact,
        )
        .one()
    )
    assert outside_item.status == StagedQueueItemStatus.READY.value
    other_item = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == seeded["other_campaign_id"])
        .one()
    )
    assert other_item.status == StagedQueueItemStatus.READY.value
    assert session.get(Campaign, seeded["other_campaign_id"]).status == CampaignStatus.RUNNING.value
    assert session.get(Campaign, seeded["campaign_id"]).template_text == before
    assert session.get(Campaign, seeded["campaign_id"]).include_products is False
    assert session.query(Message).filter(Message.product_snapshot_id.isnot(None)).count() == 0


@pytest.mark.asyncio
async def test_emergency_stop_blocks_claim_pop_and_attempt(
    pg_session_factory, enable_real_push, bridge_redis
):
    session = pg_session_factory()
    seeded = _seed(session, cohort=3, extra=1)
    result = await push_staged_items_to_worker_queue(
        session,
        batch_size=10,
        campaign_id=seeded["campaign_id"],
        refill_staging=False,
        safety_pause_others=False,
    )
    assert result["pushed"] == 0
    assert bridge_redis.calls == []
    ready = (
        session.query(StagedQueueItem)
        .filter(StagedQueueItem.campaign_id == seeded["campaign_id"])
        .count()
    )
    assert ready == 4
    from pathlib import Path

    worker = Path(__file__).resolve().parents[2].joinpath("workers", "multi_account_worker.py")
    source = worker.read_text(encoding="utf-8")
    assert source.index("if await self.check_kill_switch():") < source.index(
        "item = await self.read_next_payload()"
    )


@pytest.mark.asyncio
async def test_second_resume_is_idempotent(pg_session_factory, enable_real_push, bridge_redis):
    session = pg_session_factory()
    seeded = _seed(session, cohort=4, extra=1)
    first = await resume_running_campaign_queue(session, seeded["campaign_id"], actor="admin")
    assert first["bridge_result"]["pushed"] == 4
    second = await resume_running_campaign_queue(session, seeded["campaign_id"], actor="admin")
    assert second["code"] == "ALREADY_QUEUED"
    assert len(bridge_redis.calls) == 4


@pytest.mark.asyncio
async def test_concurrent_resume_does_not_duplicate(
    pg_session_factory, enable_real_push, bridge_redis
):
    session = pg_session_factory()
    seeded = _seed(session, cohort=8, extra=1)
    session.close()
    barrier = Barrier(2)
    results: list[dict] = []

    def _run():
        worker = pg_session_factory()
        barrier.wait()
        results.append(
            asyncio.run(
                push_staged_items_to_worker_queue(
                    worker,
                    batch_size=8,
                    campaign_id=seeded["campaign_id"],
                    refill_staging=False,
                    safety_pause_others=False,
                )
            )
        )
        worker.close()

    bridge_redis.kv[kill_switch_key()] = "false"
    threads = [Thread(target=_run), Thread(target=_run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(int(item["pushed"]) for item in results) == 8
    assert len({json.loads(raw)["message_id"] for _key, raw in bridge_redis.calls}) == 8


@pytest.mark.asyncio
async def test_existing_lease_is_not_pushed_again(
    pg_session_factory, enable_real_push, bridge_redis
):
    session = pg_session_factory()
    seeded = _seed(session, cohort=1, extra=0)
    message = session.query(Message).filter(Message.campaign_id == seeded["campaign_id"]).one()
    bridge_redis.kv[kill_switch_key()] = "false"
    bridge_redis.kv[rubika_send_lease_key(message.id)] = "already-held"
    result = await push_staged_items_to_worker_queue(
        session,
        batch_size=5,
        campaign_id=seeded["campaign_id"],
        refill_staging=False,
        safety_pause_others=False,
    )
    assert result["pushed"] == 0
    assert bridge_redis.calls == []
    item = session.query(StagedQueueItem).filter_by(campaign_id=seeded["campaign_id"]).one()
    assert item.status == StagedQueueItemStatus.READY.value


@pytest.mark.asyncio
async def test_closed_window_does_not_disable_stop(
    pg_session_factory, enable_real_push, bridge_redis, monkeypatch
):
    def _closed(_db):
        return False, None

    monkeypatch.setattr(
        "core_engine.services.campaign_send_safety.rubika_window_open",
        _closed,
    )
    session = pg_session_factory()
    seeded = _seed(session, cohort=2, extra=1)
    result = await resume_running_campaign_queue(session, seeded["campaign_id"], actor="admin")
    assert result["code"] == "WINDOW_CLOSED"
    assert result["accepted"] is False
    assert bridge_redis.kv[kill_switch_key()] == "true"
    assert bridge_redis.calls == []


@pytest.mark.asyncio
async def test_database_error_restores_stop_and_rolls_back(
    pg_session_factory, enable_real_push, bridge_redis, monkeypatch
):
    async def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "core_engine.services.campaign_control.push_staged_items_to_worker_queue",
        _boom,
    )
    session = pg_session_factory()
    seeded = _seed(session, cohort=2, extra=1)
    result = await resume_running_campaign_queue(session, seeded["campaign_id"], actor="admin")
    assert result["code"] == "RESUME_FAILED"
    assert bridge_redis.kv[kill_switch_key()] == "true"
    ready = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == seeded["campaign_id"],
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    assert ready == 3
    assert session.get(Campaign, seeded["campaign_id"]).status == CampaignStatus.RUNNING.value


def test_lock_timeout_returns_without_freezing_other_sessions(pg_session_factory):
    holder = pg_session_factory()
    seeded = _seed(holder, cohort=1, extra=0)
    row = holder.query(Campaign).filter(Campaign.id == seeded["campaign_id"]).one()
    row.title = "held"
    holder.flush()
    waiter = pg_session_factory()
    try:
        waiter.execute(text("SET lock_timeout = '1s'"))
        with pytest.raises(OperationalError):
            waiter.query(Campaign).filter(Campaign.id == seeded["campaign_id"]).with_for_update().one()
        reader = pg_session_factory()
        assert reader.execute(text("SELECT 1")).scalar() == 1
        reader.close()
    finally:
        holder.rollback()
        waiter.close()


def test_resume_route_is_admin_only_and_is_not_start():
    from pathlib import Path

    source = Path(__file__).resolve().parents[2].joinpath("core_engine", "api", "campaigns.py")
    text_value = source.read_text(encoding="utf-8")
    resume_at = text_value.index('"/{campaign_id}/resume-queue"')
    window = text_value[resume_at : resume_at + 700]
    assert "RoleType.ADMIN" in window
    assert "RoleType.OPERATOR" not in window
    assert "start_campaign(" not in window
