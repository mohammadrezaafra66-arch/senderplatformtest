"""Phase 6 — controlled dispatch, fairness, pause, in-flight, no duplicate claim.

No live Rubika send. Queue push is mocked at Redis.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.config import get_settings
from core_engine.database import Base
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignAccount,
    CampaignStatus,
    Contact,
    CampaignRecipient,
    Message,
    MessageAttempt,
    PlatformType,
    RenderedMessage,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.campaign_dispatch import rotate_campaign_ids
from core_engine.services.campaign_inflight import (
    acquire_send_lease,
    release_account_inflight,
    reserve_account_inflight,
)
from core_engine.services.campaign_render import hash_final_text
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue
from core_engine.services.rubika_circuit import close_circuit, open_circuit
from workers.retry import build_retry_queue_payload


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for Postgres-backed Phase 6 dispatch tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for Phase 6 dispatch tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


@pytest.fixture(autouse=True)
def _cleanup_db(pg_session_factory):
    """Remove Postgres rows created by this file to keep other phases hermetic."""

    yield

    session = pg_session_factory()
    try:
        campaign_ids = [
            row_id
            for (row_id,) in session.query(Campaign.id)
            .filter(Campaign.title == "p6d")
            .all()
        ]
        if not campaign_ids:
            return

        contact_ids = [
            row_id
            for (row_id,) in session.query(StagedQueueItem.contact_id)
            .filter(StagedQueueItem.campaign_id.in_(campaign_ids))
            .distinct()
            .all()
        ]
        account_ids = [
            row_id
            for (row_id,) in session.query(Message.account_id)
            .filter(Message.campaign_id.in_(campaign_ids))
            .distinct()
            .all()
        ]

        session.query(StagedQueueItem).filter(
            StagedQueueItem.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(RenderedMessage).filter(
            RenderedMessage.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        session.query(CampaignRecipient).filter(
            CampaignRecipient.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)
        session.query(CampaignAccount).filter(
            CampaignAccount.campaign_id.in_(campaign_ids)
        ).delete(synchronize_session=False)

        message_ids = [
            row_id
            for (row_id,) in session.query(Message.id)
            .filter(Message.campaign_id.in_(campaign_ids))
            .all()
        ]
        if message_ids:
            session.query(MessageAttempt).filter(
                MessageAttempt.message_id.in_(message_ids)
            ).delete(synchronize_session=False)
        session.query(Message).filter(Message.campaign_id.in_(campaign_ids)).delete(
            synchronize_session=False
        )

        session.query(Campaign).filter(Campaign.id.in_(campaign_ids)).delete(
            synchronize_session=False
        )

        if contact_ids:
            session.query(Contact).filter(Contact.id.in_(contact_ids)).delete(
                synchronize_session=False
            )
        if account_ids:
            session.query(Account).filter(Account.id.in_(account_ids)).delete(
                synchronize_session=False
            )

        session.commit()
    finally:
        session.close()


class BridgeRedis:
    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []
        self.kv: dict[str, str] = {}
        self.zset: dict[str, float] = {}
        self.lists: dict[str, list[str]] = {}

    async def ping(self):
        return True

    async def scard(self, key: str) -> int:
        return 0

    async def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    async def incr(self, key: str) -> int:
        with self._lock:
            n = int(self.kv.get(key, "0")) + 1
            self.kv[key] = str(n)
            return n

    async def get(self, key: str):
        return self.kv.get(key)

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def set(self, key: str, value: str, ex: int | None = None):
        self.kv[key] = value
        return True

    async def delete(self, key: str):
        self.kv.pop(key, None)
        return 1

    async def mget(self, keys):
        return [self.kv.get(k) for k in keys]

    async def rpush(self, key: str, value: str):
        with self._lock:
            self.calls.append((key, value))
            self.lists.setdefault(key, []).append(value)
        return 1

    async def zadd(self, key: str, mapping: dict):
        self.zset.update(mapping)
        return len(mapping)

    async def zrangebyscore(self, key, min=0, max=0, start=0, num=100):
        due = [m for m, s in self.zset.items() if s <= float(max)]
        return due[int(start) : int(start) + int(num)]

    async def zrem(self, key, member):
        self.zset.pop(member, None)
        return 1

    async def eval(self, script, numkeys, *args):
        keys = args[:numkeys]
        argv = args[numkeys:]
        text = str(script)
        if "DECR" in text:
            member_key = keys[1]
            if member_key not in self.kv:
                return 0
            self.kv.pop(member_key, None)
            count_key = keys[0]
            current = int(self.kv.get(count_key, "0"))
            if current > 0:
                self.kv[count_key] = str(current - 1)
            return 1
        if "INCR" in text:
            count_key, member_key = keys[0], keys[1]
            max_n = int(argv[0])
            if member_key in self.kv:
                return ["ok", "duplicate", self.kv.get(count_key, "0")]
            current = int(self.kv.get(count_key, "0"))
            if current >= max_n:
                return ["deny", "max", str(current)]
            current += 1
            self.kv[count_key] = str(current)
            self.kv[member_key] = "1"
            return ["ok", "reserved", str(current)]
        key = keys[0]
        token = str(argv[1])
        current = self.kv.get(key)
        if current and current != token:
            return ["deny", current]
        self.kv[key] = token
        return ["ok", token]


@pytest.fixture
def enable_real_push(monkeypatch):
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bridge_redis(monkeypatch):
    fake = BridgeRedis()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_redis_client",
        lambda: fake,
    )
    monkeypatch.setattr(
        "core_engine.services.campaign_inflight.get_redis_client",
        lambda: fake,
        raising=False,
    )
    return fake


def _isolate_running(session):
    session.query(Campaign).filter(Campaign.status == CampaignStatus.RUNNING.value).update(
        {Campaign.status: CampaignStatus.PAUSED.value},
        synchronize_session=False,
    )
    session.commit()


def _seed_running(session, *, n: int, platform=PlatformType.BALE, channel="bale", name=None):
    campaign = Campaign(
        name=name or f"p6d-{uuid.uuid4().hex[:8]}",
        title="p6d",
        channel=channel,
        platform=platform,
        status=CampaignStatus.RUNNING.value,
        template_text="hi",
    )
    session.add(campaign)
    session.flush()
    account = Account(
        platform=platform,
        phone_number=f"+98{uuid.uuid4().hex[:10]}",
        label="disp",
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()
    for i in range(n):
        contact = Contact(
            first_name=f"n{i}",
            phone=f"+989{uuid.uuid4().hex[:10]}",
            phone_e164=f"+989{uuid.uuid4().hex[:10]}",
            consent_status="allowed",
        )
        session.add(contact)
        session.flush()
        text = f"hello-{i}"
        digest = hash_final_text(text)
        rendered = RenderedMessage(
            campaign_id=campaign.id,
            contact_id=contact.id,
            channel=channel,
            final_text=text,
            render_mode="template",
            ready_for_queue=True,
            queue_payload={"metadata": {"final_text_sha256": digest}},
        )
        session.add(rendered)
        session.flush()
        message = Message(
            campaign_id=campaign.id,
            account_id=account.id,
            contact_id=contact.id,
            rendered_text=text,
            dedupe_key=f"p6d:{campaign.id}:{contact.id}:{uuid.uuid4().hex[:6]}",
        )
        session.add(message)
        session.flush()
        session.add(
            StagedQueueItem(
                campaign_id=campaign.id,
                contact_id=contact.id,
                rendered_message_id=rendered.id,
                channel=channel,
                status=StagedQueueItemStatus.READY.value,
                final_text=text,
                queue_payload={
                    "message_id": message.id,
                    "account_id": account.id,
                    "campaign_id": campaign.id,
                    "contact_id": contact.id,
                    "platform": channel,
                    "final_text": text,
                    "message_text": text,
                    "dedupe_key": message.dedupe_key,
                    "metadata": {"final_text_sha256": digest, "render_version": "campaign-render-v1"},
                },
            )
        )
    session.commit()
    return campaign, account


@pytest.mark.asyncio
async def test_fairness_two_campaigns(pg_session_factory, enable_real_push, bridge_redis, monkeypatch):
    session = pg_session_factory()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    _isolate_running(session)
    big, _ = _seed_running(session, n=40, name="big")
    small, _ = _seed_running(session, n=8, name="small")
    result = await push_staged_items_to_worker_queue(session, batch_size=20)
    campaigns = {json.loads(raw)["campaign_id"] for _, raw in bridge_redis.calls}
    counts: dict[int, int] = {}
    for _, raw in bridge_redis.calls:
        cid = int(json.loads(raw)["campaign_id"])
        counts[cid] = counts.get(cid, 0) + 1
    assert result["pushed"] >= 2
    assert counts.get(int(big.id), 0) >= 1
    assert counts.get(int(small.id), 0) >= 1
    session.close()


@pytest.mark.asyncio
async def test_pause_stops_new_dispatch(pg_session_factory, enable_real_push, bridge_redis, monkeypatch):
    session = pg_session_factory()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    _isolate_running(session)
    campaign, _ = _seed_running(session, n=6)
    first = await push_staged_items_to_worker_queue(session, batch_size=3)
    assert first["pushed"] == 3
    campaign.status = CampaignStatus.PAUSED.value
    session.commit()
    second = await push_staged_items_to_worker_queue(session, batch_size=10)
    assert second["pushed"] == 0
    campaign.status = CampaignStatus.RUNNING.value
    session.commit()
    third = await push_staged_items_to_worker_queue(session, batch_size=10)
    assert third["pushed"] == 3
    ids = [json.loads(raw)["message_id"] for _, raw in bridge_redis.calls]
    assert len(ids) == len(set(ids))
    session.close()


@pytest.mark.asyncio
async def test_circuit_open_stops_rubika_not_permanent(pg_session_factory, enable_real_push, bridge_redis, monkeypatch):
    session = pg_session_factory()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    _isolate_running(session)
    campaign, _ = _seed_running(session, n=4, platform=PlatformType.RUBIKA, channel="rubika")
    await open_circuit(bridge_redis, reason="p6-disp", open_seconds=60)
    result = await push_staged_items_to_worker_queue(session, batch_size=10)
    assert result["pushed"] == 0
    ready = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    skipped_perm = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == campaign.id,
            StagedQueueItem.status == StagedQueueItemStatus.SKIPPED.value,
        )
        .count()
    )
    assert ready == 4
    assert skipped_perm == 0
    await close_circuit(bridge_redis, reason="p6-disp-close")
    session.close()


@pytest.mark.asyncio
async def test_quarantine_dispatch_no_silent_reassign(
    pg_session_factory, enable_real_push, bridge_redis, monkeypatch
):
    session = pg_session_factory()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    _isolate_running(session)
    healthy, _ = _seed_running(session, n=3, platform=PlatformType.RUBIKA, channel="rubika")
    sick, sick_account = _seed_running(session, n=3, platform=PlatformType.RUBIKA, channel="rubika")
    from workers.redis_keys import rubika_quarantine_key

    await bridge_redis.set(rubika_quarantine_key(sick_account.id), '{"reason":"p6"}')
    result = await push_staged_items_to_worker_queue(session, batch_size=20)
    pushed_campaigns = {json.loads(raw)["campaign_id"] for _, raw in bridge_redis.calls}
    pushed_accounts = {json.loads(raw)["account_id"] for _, raw in bridge_redis.calls}
    assert result["pushed"] >= 1
    assert int(healthy.id) in pushed_campaigns
    assert sick_account.id not in pushed_accounts
    sick_ready = (
        session.query(StagedQueueItem)
        .filter(
            StagedQueueItem.campaign_id == sick.id,
            StagedQueueItem.status == StagedQueueItemStatus.READY.value,
        )
        .count()
    )
    assert sick_ready == 3
    session.close()


@pytest.mark.asyncio
async def test_concurrent_claim_no_duplicate(pg_session_factory, enable_real_push, monkeypatch):
    session = pg_session_factory()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    fake = BridgeRedis()
    monkeypatch.setattr("core_engine.services.queue_bridge.get_redis_client", lambda: fake)
    _isolate_running(session)
    _seed_running(session, n=12)
    from core_engine.database import Base
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=session.bind)

    async def worker():
        s = factory()
        try:
            return await push_staged_items_to_worker_queue(s, batch_size=10)
        finally:
            s.close()

    r1, r2 = await asyncio.gather(worker(), worker())
    total = r1["pushed"] + r2["pushed"]
    assert total == 12
    payloads = [json.loads(raw)["message_id"] for _, raw in fake.calls]
    assert len(payloads) == len(set(payloads))
    session.close()


@pytest.mark.asyncio
async def test_inflight_ttl_and_lease(bridge_redis):
    ok = await reserve_account_inflight(bridge_redis, 9, 100, max_in_flight=1, ttl_seconds=30)
    assert ok is True
    denied = await reserve_account_inflight(bridge_redis, 9, 101, max_in_flight=1, ttl_seconds=30)
    assert denied is False
    await release_account_inflight(bridge_redis, 9, 100)
    ok2 = await reserve_account_inflight(bridge_redis, 9, 101, max_in_flight=1, ttl_seconds=30)
    assert ok2 is True
    lease = await acquire_send_lease(bridge_redis, 55, token="w1", ttl_seconds=30)
    assert lease is True
    lease2 = await acquire_send_lease(bridge_redis, 55, token="w2", ttl_seconds=30)
    assert lease2 is False


def test_retry_payload_preserves_frozen_text():
    raw = json.dumps(
        {
            "message_id": 1,
            "final_text": "متن فریز",
            "message_text": "متن فریز",
            "account_id": 3,
            "attempt": 1,
            "metadata": {"final_text_sha256": hash_final_text("متن فریز")},
        },
        ensure_ascii=False,
    )
    from workers.payloads import WorkerPayload

    payload = WorkerPayload.model_validate(
        {
            "message_id": 1,
            "campaign_id": 1,
            "contact_id": 1,
            "account_id": 3,
            "platform": "rubika",
            "recipient": "1",
            "recipient_type": "phone",
            "message_text": "متن فریز",
            "dedupe_key": "x",
            "attempt": 1,
        }
    )
    updated = json.loads(build_retry_queue_payload(raw, payload))
    assert updated["final_text"] == "متن فریز"
    assert updated["metadata"]["final_text_sha256"] == hash_final_text("متن فریز")
    assert updated["attempt"] == 2


def test_rotate_campaign_ids_fair():
    assert rotate_campaign_ids([1, 2, 3], 1) == [2, 3, 1]


@pytest.mark.asyncio
async def test_dispatch_does_not_call_gpt_or_products(pg_session_factory, enable_real_push, bridge_redis, monkeypatch):
    gpt = MagicMock()
    products = MagicMock()
    monkeypatch.setattr(
        "core_engine.services.message_variation.service.generate_validated_pool",
        gpt,
        raising=False,
    )
    monkeypatch.setattr(
        "core_engine.services.product_feed.service.fetch_current_advertising_products",
        products,
        raising=False,
    )
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_consent_block_reason",
        lambda *a, **k: None,
    )
    session = pg_session_factory()
    _isolate_running(session)
    campaign, _ = _seed_running(session, n=5)
    hashes_before = {
        item.id: hash_final_text(item.final_text)
        for item in session.query(StagedQueueItem).filter(StagedQueueItem.campaign_id == campaign.id)
    }
    await push_staged_items_to_worker_queue(session, batch_size=5)
    gpt.assert_not_called()
    products.assert_not_called()
    session.expire_all()
    for item in session.query(StagedQueueItem).filter(StagedQueueItem.campaign_id == campaign.id):
        assert hash_final_text(item.final_text) == hashes_before[item.id]
    session.close()
