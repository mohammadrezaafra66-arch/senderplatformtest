import pytest

from workers.distributed_lock import RedisDistributedLock


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script, numkeys, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


@pytest.mark.asyncio
async def test_distributed_lock_acquire_and_release():
    redis = FakeRedis()
    lock = RedisDistributedLock(redis, "lock:test:1", ttl_seconds=30)
    assert await lock.acquire() is True
    assert lock.held is True

    second = RedisDistributedLock(redis, "lock:test:1", ttl_seconds=30)
    assert await second.acquire() is False

    await lock.release()
    assert lock.held is False
    assert await second.acquire() is True
