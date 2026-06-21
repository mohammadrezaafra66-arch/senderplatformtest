import asyncio
import json
import os
import time

import pytest

from workers.config import WorkerSettings, get_worker_settings
from workers.pool_factory import build_pool_worker
from workers.whatsapp_pool_worker import WhatsAppPoolWorker


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, list[str]] = {}
        self.values: dict[str, str] = {}
        self.ttl_map: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.ttl_map[key] = ex
        return True

    async def ttl(self, key: str):
        return self.ttl_map.get(key, -2)

    async def incr(self, key: str):
        current = int(self.values.get(key, "0"))
        current += 1
        self.values[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int):
        self.ttl_map[key] = seconds
        return True

    async def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0

    async def lpop(self, key: str) -> str | None:
        items = self.store.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def lpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).insert(0, value)
        return len(self.store[key])

    async def rpush(self, key: str, value: str) -> int:
        self.store.setdefault(key, []).append(value)
        return len(self.store[key])


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(
        "workers.multi_account_worker.Redis.from_url",
        lambda *args, **kwargs: fake,
    )
    return fake


@pytest.fixture
def dry_run_settings(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("WHATSAPP_ACCOUNT_IDS", "1,2")
    monkeypatch.setenv("WORKER_POOL_SIZE", "1")
    monkeypatch.setenv("WORKER_POOL_INDEX", "0")
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("WHATSAPP_DISTRIBUTED_LOCK_ENABLED", "false")
    get_worker_settings.cache_clear()
    yield
    get_worker_settings.cache_clear()


def _staged_payload(*, account_id: int, campaign_id: int, contact_id: int) -> dict:
    return {
        "campaign_id": campaign_id,
        "contact_id": contact_id,
        "channel": "whatsapp",
        "final_text": f"hello from account {account_id}",
        "phone": "+989120000099",
        "account_id": account_id,
    }


@pytest.mark.asyncio
async def test_whatsapp_pool_worker_round_robin_accounts(
    fake_redis,
    dry_run_settings,
    pg_session_factory,
    recipient_bundle,
):
    from core_engine.models import Campaign, CampaignRecipient, Contact, PlatformType, SendStatus

    campaign_id, contact_id, _session = recipient_bundle
    database_url = os.environ["DATABASE_URL"]

    session = pg_session_factory()
    try:
        campaign = session.get(Campaign, campaign_id)
        campaign.platform = PlatformType.WHATSAPP
        campaign.channel = "whatsapp"
        session.commit()
    finally:
        session.close()

    fake_redis.store["queue:whatsapp:1"] = [
        json.dumps(_staged_payload(account_id=1, campaign_id=campaign_id, contact_id=contact_id))
    ]
    fake_redis.store["queue:whatsapp:2"] = [
        json.dumps(
            _staged_payload(account_id=2, campaign_id=campaign_id, contact_id=contact_id),
            ensure_ascii=False,
        )
    ]

    worker = WhatsAppPoolWorker(
        account_ids=[1, 2],
        redis_url="redis://localhost:6379/0",
        database_url=database_url,
        poll_interval_seconds=1,
        settings=WorkerSettings(
            DRY_RUN=True,
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
            WHATSAPP_MIN_SEND_DELAY_SECONDS=0,
            WORKER_HEARTBEAT_INTERVAL_SECONDS=0,
        ),
    )

    await worker.run_once()
    await worker.run_once()

    check_session = pg_session_factory()
    try:
        recipient = (
            check_session.query(CampaignRecipient)
            .filter(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.contact_id == contact_id,
            )
            .first()
        )
        assert recipient is not None
        assert recipient.send_status == SendStatus.DRY_RUN
    finally:
        check_session.close()


@pytest.mark.asyncio
async def test_whatsapp_pool_worker_rejects_unassigned_account_id(fake_redis, dry_run_settings):
    database_url = os.environ.get("DATABASE_URL", "postgresql://local/test")
    fake_redis.store["queue:whatsapp:1"] = [
        json.dumps(_staged_payload(account_id=99, campaign_id=1, contact_id=2))
    ]

    worker = WhatsAppPoolWorker(
        account_ids=[1, 2],
        redis_url="redis://localhost:6379/0",
        database_url=database_url,
        settings=WorkerSettings(
            DRY_RUN=True,
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
            WHATSAPP_MIN_SEND_DELAY_SECONDS=0,
        ),
    )
    await worker.run_once()
    assert fake_redis.store["queue:whatsapp:1"] == []


@pytest.mark.asyncio
async def test_whatsapp_pool_browser_lock_serializes_send(monkeypatch, fake_redis):
    database_url = os.environ.get("DATABASE_URL", "postgresql://local/test")
    call_times: list[float] = []

    async def slow_deliver(platform, payload, settings):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.05)
        from workers.payloads import WorkerResult

        return WorkerResult(success=True, status="dry_run", platform_message_id="x")

    monkeypatch.setattr("workers.whatsapp_pool_worker.deliver_platform_message", slow_deliver)

    worker = WhatsAppPoolWorker(
        account_ids=[1],
        redis_url="redis://localhost:6379/0",
        database_url=database_url,
        browser_lock_enabled=True,
        settings=WorkerSettings(
            DRY_RUN=True,
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
            WHATSAPP_MIN_SEND_DELAY_SECONDS=0,
        ),
    )
    worker._redis = fake_redis

    from workers.payloads import WorkerPayload

    payload = WorkerPayload.model_validate(
        {
            "message_id": 1,
            "campaign_id": 1,
            "contact_id": 2,
            "account_id": 1,
            "platform": "whatsapp",
            "recipient": "09120000000",
            "recipient_type": "phone_number",
            "message_text": "a",
            "dedupe_key": "d1",
        }
    )

    await asyncio.gather(worker.send_message(payload), worker.send_message(payload))
    assert len(call_times) == 2
    assert call_times[1] - call_times[0] >= 0.04


def test_build_pool_worker_assigns_accounts(monkeypatch):
    monkeypatch.setenv("WORKER_PLATFORM", "whatsapp")
    monkeypatch.setenv("WHATSAPP_ACCOUNT_IDS", "1,2,3,4")
    monkeypatch.setenv("WORKER_POOL_SIZE", "2")
    monkeypatch.setenv("WORKER_POOL_INDEX", "0")
    get_worker_settings.cache_clear()

    worker = build_pool_worker()
    assert isinstance(worker, WhatsAppPoolWorker)
    assert worker.account_ids == [2, 4]

    get_worker_settings.cache_clear()


def test_build_pool_worker_rejects_non_whatsapp_platform(monkeypatch):
    monkeypatch.setenv("WORKER_PLATFORM", "bale")
    get_worker_settings.cache_clear()

    with pytest.raises(ValueError, match="only supported for WORKER_PLATFORM=whatsapp"):
        build_pool_worker()

    get_worker_settings.cache_clear()
