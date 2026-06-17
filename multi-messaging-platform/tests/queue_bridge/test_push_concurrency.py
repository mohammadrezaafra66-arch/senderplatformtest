import asyncio
import os
import threading
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_engine.config import Settings, get_settings
from core_engine.database import Base
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    CampaignStatus,
    Contact,
    OptEvent,
    PlatformType,
    StagedQueueItem,
    StagedQueueItemStatus,
)
from core_engine.services.queue_bridge import push_staged_items_to_worker_queue


class FakeRedis:
    def __init__(self):
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    async def rpush(self, key: str, value: str):
        with self._lock:
            self.calls.append((key, value))
        return 1


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for Postgres-backed concurrency tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for concurrency tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


@pytest.fixture
def enable_real_push(monkeypatch):
    monkeypatch.setenv("REAL_QUEUE_PUSH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        "core_engine.services.queue_bridge.get_redis_client",
        lambda: fake,
    )
    return fake


def _seed_campaign_bundle(session):
    campaign = Campaign(
        name="t-queue-bridge",
        channel="bale",
        title="t-queue-bridge",
        platform=PlatformType.BALE,
        status=CampaignStatus.RUNNING.value,
    )
    session.add(campaign)
    session.flush()
    return campaign


def _seed_accounts(session, platform: PlatformType, count: int = 2):
    accounts: list[Account] = []
    for i in range(count):
        acc = Account(
            platform=platform,
            label=f"acc-{i}",
            status=AccountStatus.ACTIVE,
        )
        session.add(acc)
        accounts.append(acc)
    session.flush()
    return accounts


def _seed_ready_items(session, campaign_id: int, n: int = 10):
    items: list[StagedQueueItem] = []
    for i in range(n):
        contact = Contact(
            phone=f"+989120000{i:03d}",
            phone_e164=f"+989120000{i:03d}",
            consent_status="unknown",
            blacklisted=False,
        )
        session.add(contact)
        session.flush()

        item = StagedQueueItem(
            campaign_id=campaign_id,
            contact_id=contact.id,
            channel="bale",
            status=StagedQueueItemStatus.READY.value,
            final_text=f"hello {i}",
            queue_payload={
                "campaign_id": campaign_id,
                "contact_id": contact.id,
                "channel": "bale",
                "final_text": f"hello {i}",
            },
            skip_reason=None,
        )
        session.add(item)
        items.append(item)
    session.flush()
    return items


@pytest.mark.integration
def test_push_concurrency_no_duplicate_push(pg_session_factory, fake_redis, enable_real_push):
    # Arrange
    s = pg_session_factory()
    try:
        campaign = _seed_campaign_bundle(s)
        _seed_accounts(s, PlatformType.BALE, count=3)
        items = _seed_ready_items(s, campaign.id, n=12)
        s.commit()
        ids = [it.id for it in items]
    finally:
        s.close()

    barrier = threading.Barrier(2)

    def worker():
        session = pg_session_factory()
        try:
            barrier.wait()
            asyncio.run(push_staged_items_to_worker_queue(session, batch_size=100))
        finally:
            session.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # Assert
    session = pg_session_factory()
    try:
        queued = (
            session.query(StagedQueueItem)
            .filter(StagedQueueItem.id.in_(ids))
            .filter(StagedQueueItem.status == StagedQueueItemStatus.QUEUED.value)
            .count()
        )
        assert queued == len(ids)

        # No duplicate push: number of redis pushes equals item count
        assert len(fake_redis.calls) == len(ids)
    finally:
        session.close()


@pytest.mark.integration
def test_consent_becomes_blocked_skips_item(pg_session_factory, fake_redis, enable_real_push):
    s = pg_session_factory()
    try:
        campaign = _seed_campaign_bundle(s)
        _seed_accounts(s, PlatformType.BALE, count=1)
        items = _seed_ready_items(s, campaign.id, n=1)
        s.commit()
        item_id = items[0].id
        contact_id = items[0].contact_id
    finally:
        s.close()

    # Make consent blocked after staging.
    s2 = pg_session_factory()
    try:
        s2.add(
            OptEvent(
                contact_id=contact_id,
                opted_in=False,
                channel=PlatformType.BALE,
                reason="test",
            )
        )
        s2.commit()
    finally:
        s2.close()

    s3 = pg_session_factory()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(s3, batch_size=10))
        assert result["skipped_consent"] == 1
        refreshed = s3.get(StagedQueueItem, item_id)
        assert refreshed.status == StagedQueueItemStatus.SKIPPED.value
        assert refreshed.skip_reason and "consent_blocked" in refreshed.skip_reason
        assert len(fake_redis.calls) == 0
    finally:
        s3.close()


@pytest.mark.integration
def test_no_active_account_returns_item_to_ready(pg_session_factory, fake_redis, enable_real_push):
    s = pg_session_factory()
    try:
        campaign = _seed_campaign_bundle(s)
        # No accounts seeded
        items = _seed_ready_items(s, campaign.id, n=2)
        s.commit()
        ids = [it.id for it in items]
    finally:
        s.close()

    s2 = pg_session_factory()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(s2, batch_size=10))
        assert result["skipped_no_account"] == 2
        states = {
            row.id: row.status
            for row in s2.query(StagedQueueItem).filter(StagedQueueItem.id.in_(ids)).all()
        }
        assert set(states.values()) == {StagedQueueItemStatus.READY.value}
        assert len(fake_redis.calls) == 0
    finally:
        s2.close()


@pytest.mark.integration
def test_disabled_flag_prevents_push(pg_session_factory, fake_redis):
    get_settings.cache_clear()
    s = pg_session_factory()
    try:
        campaign = _seed_campaign_bundle(s)
        _seed_accounts(s, PlatformType.BALE, count=1)
        items = _seed_ready_items(s, campaign.id, n=3)
        s.commit()
        ids = [it.id for it in items]
    finally:
        s.close()

    s2 = pg_session_factory()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(s2, batch_size=100))
        assert result["pushed"] == 0
        assert result["skipped_consent"] == 0
        assert result["skipped_no_account"] == 0
        assert result["failed"] == 0
        assert len(fake_redis.calls) == 0

        states = {
            row.id: row.status
            for row in s2.query(StagedQueueItem).filter(StagedQueueItem.id.in_(ids)).all()
        }
        assert set(states.values()) == {StagedQueueItemStatus.READY.value}
    finally:
        s2.close()


@pytest.mark.integration
def test_non_running_campaign_is_skipped(pg_session_factory, fake_redis, enable_real_push):
    s = pg_session_factory()
    try:
        campaign = _seed_campaign_bundle(s)
        campaign.status = CampaignStatus.DRAFT.value
        _seed_accounts(s, PlatformType.BALE, count=1)
        items = _seed_ready_items(s, campaign.id, n=4)
        s.commit()
        ids = [it.id for it in items]
    finally:
        s.close()

    s2 = pg_session_factory()
    try:
        result = asyncio.run(push_staged_items_to_worker_queue(s2, batch_size=100))
        assert result["pushed"] == 0
        assert len(fake_redis.calls) == 0

        states = {
            row.id: row.status
            for row in s2.query(StagedQueueItem).filter(StagedQueueItem.id.in_(ids)).all()
        }
        assert set(states.values()) == {StagedQueueItemStatus.READY.value}
    finally:
        s2.close()

