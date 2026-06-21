import json

import pytest

from workers.pool_health import publish_worker_heartbeat, resolve_worker_hostname
from workers.redis_keys import worker_heartbeat_key


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expiry: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.expiry[key] = ex
        return True


@pytest.mark.asyncio
async def test_publish_worker_heartbeat():
    redis = FakeRedis()
    hostname = resolve_worker_hostname()
    await publish_worker_heartbeat(
        redis,
        platform="whatsapp",
        hostname=hostname,
        assigned_account_ids=[1, 3],
        pool_size=2,
        pool_index=1,
        ttl_seconds=45,
    )
    key = worker_heartbeat_key("whatsapp", hostname)
    assert key in redis.values
    payload = json.loads(redis.values[key])
    assert payload["assigned_account_ids"] == [1, 3]
    assert payload["pool_size"] == 2
    assert redis.expiry[key] == 45
