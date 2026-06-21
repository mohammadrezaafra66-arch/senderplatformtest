import pytest

from workers.config import WorkerSettings
from workers.payloads import WorkerResult
from workers.rate_limit import (
    hourly_send_count,
    is_hourly_cap_reached,
    is_min_delay_active,
    record_successful_send,
    set_min_delay,
)
from workers.redis_keys import delay_key
from workers.whatsapp_pool_worker import WhatsAppPoolWorker


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttl_map: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str):
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

    async def lpop(self, key: str):
        items = self.lists.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def lpush(self, key: str, value: str):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def rpush(self, key: str, value: str):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


@pytest.mark.asyncio
async def test_min_delay_and_hourly_cap_helpers():
    redis = FakeRedis()
    await set_min_delay(redis, 1, 10)
    assert await is_min_delay_active(redis, 1) is True
    assert delay_key(1) in redis.ttl_map

    await record_successful_send(redis, 1)
    assert await hourly_send_count(redis, 1) == 1
    assert await is_hourly_cap_reached(redis, 1, hourly_cap=1) is True


@pytest.mark.asyncio
async def test_whatsapp_pool_send_throttled_when_min_delay_active(monkeypatch):
    redis = FakeRedis()
    await set_min_delay(redis, 1, 30)

    worker = WhatsAppPoolWorker(
        account_ids=[1],
        redis_url="redis://localhost:6379/0",
        database_url="postgresql://local/test",
        settings=WorkerSettings(
            DRY_RUN=True,
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
        ),
    )
    worker._redis = redis

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
            "message_text": "hello",
            "dedupe_key": "d1",
        }
    )

    result = await worker.send_message(payload)
    assert result.success is False
    assert result.error_code == "whatsapp_send_throttled"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_whatsapp_pool_retry_requeues_payload(monkeypatch):
    import os

    redis = FakeRedis()

    async def failing_deliver(platform, payload, settings):
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_web_send_failed",
            error_message="temporary",
            retryable=True,
        )

    monkeypatch.setattr("workers.whatsapp_pool_worker.deliver_platform_message", failing_deliver)
    monkeypatch.setattr("workers.multi_account_worker.update_message_attempt_result", lambda **kwargs: None)

    worker = WhatsAppPoolWorker(
        account_ids=[1],
        redis_url="redis://localhost:6379/0",
        database_url=os.environ.get("DATABASE_URL", "postgresql://local/test"),
        max_retry_attempts=3,
        retry_base_delay_seconds=0,
        settings=WorkerSettings(
            DRY_RUN=False,
            WHATSAPP_DISTRIBUTED_LOCK_ENABLED=False,
            WHATSAPP_MIN_SEND_DELAY_SECONDS=0,
        ),
    )
    worker._redis = redis

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
            "message_text": "hello",
            "dedupe_key": "d1",
            "attempt": 1,
        }
    )
    raw = '{"campaign_id":1,"contact_id":2,"channel":"whatsapp","final_text":"hello","phone":"09120000000","account_id":1,"attempt":1}'

    result = await worker.send_message(payload)
    await worker.handle_result(payload, result, raw_payload=raw, account_id=1)

    queued = redis.lists.get("queue:whatsapp:1") or []
    assert len(queued) == 1
    assert '"attempt": 2' in queued[0] or '"attempt":2' in queued[0]
