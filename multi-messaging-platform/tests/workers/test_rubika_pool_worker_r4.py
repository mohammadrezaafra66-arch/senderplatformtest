"""R4 — Rubika multi-account pool worker fair polling + pause-before-LPOP."""

from __future__ import annotations

import json

import pytest

from workers.multi_account_worker import MultiAccountWorker
from workers.payloads import WorkerPayload, WorkerResult
from workers.redis_flags import set_account_pause
from workers.redis_keys import queue_key
from workers.rubika_pool_worker import RubikaPoolWorker


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.lpop_by_key: dict[str, int] = {}

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.kv[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.kv else 0

    async def delete(self, key: str) -> int:
        return 1 if self.kv.pop(key, None) is not None else 0

    async def lpop(self, key: str) -> str | None:
        self.lpop_by_key[key] = self.lpop_by_key.get(key, 0) + 1
        items = self.lists.get(key) or []
        if not items:
            return None
        return items.pop(0)

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])


class StubRubikaPool(RubikaPoolWorker):
    def __init__(self, *, account_ids: list[int], fake: FakeRedis):
        MultiAccountWorker.__init__(
            self,
            platform="rubika",
            account_ids=account_ids,
            redis_url="redis://test",
            database_url="postgresql://test",
            poll_interval_seconds=1,
            execution_enabled=True,
        )
        self._redis = fake
        self._settings = None
        self._account_refresh_interval_seconds = 0
        self._heartbeat_interval_seconds = 15
        self._heartbeat_ttl_seconds = 45
        self._hostname = "test-host"
        self._heartbeat_task = None
        self._account_refresh_task = None
        self._explicit_account_ids = list(account_ids)
        self.sent: list[int] = []

    async def send_message(self, payload: WorkerPayload) -> WorkerResult:
        self.sent.append(int(payload.account_id))
        return WorkerResult(success=True, status="sent", platform_message_id="x")


def _payload(account_id: int, message_id: int) -> str:
    return json.dumps(
        {
            "message_id": message_id,
            "campaign_id": 4,
            "contact_id": 1,
            "account_id": account_id,
            "platform": "rubika",
            "recipient": "+989120000000",
            "recipient_type": "private",
            "message_text": "hello",
            "dedupe_key": f"d-{message_id}",
            "attempt": 0,
        }
    )


@pytest.mark.asyncio
async def test_fair_round_robin_no_cross_account():
    fake = FakeRedis()
    worker = StubRubikaPool(account_ids=[12, 79], fake=fake)
    fake.lists[queue_key("rubika", 12)] = [_payload(12, 101), _payload(12, 102)]
    fake.lists[queue_key("rubika", 79)] = [_payload(79, 201)]

    await worker.run_once()
    await worker.run_once()
    await worker.run_once()

    assert worker.sent == [12, 79, 12]
    assert fake.lpop_by_key[queue_key("rubika", 12)] == 2
    assert fake.lpop_by_key[queue_key("rubika", 79)] == 1


@pytest.mark.asyncio
async def test_paused_account_zero_lpop():
    fake = FakeRedis()
    worker = StubRubikaPool(account_ids=[12, 79], fake=fake)
    await set_account_pause(fake, 12, paused=True)
    fake.kv[f"account:12:paused"] = "1"
    fake.lists[queue_key("rubika", 12)] = [_payload(12, 101)]
    fake.lists[queue_key("rubika", 79)] = [_payload(79, 201)]

    await worker.run_once()
    assert worker.sent == [79]
    assert fake.lpop_by_key.get(queue_key("rubika", 12), 0) == 0
    assert fake.lpop_by_key.get(queue_key("rubika", 79), 0) == 1
    assert len(fake.lists[queue_key("rubika", 12)]) == 1
