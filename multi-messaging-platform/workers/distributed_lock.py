"""Redis distributed locks for cross-replica worker coordination."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class RedisDistributedLock:
    """Simple SET NX EX lock with token-safe release."""

    def __init__(
        self,
        redis: Redis,
        key: str,
        *,
        ttl_seconds: int,
        token: str | None = None,
    ) -> None:
        self._redis = redis
        self._key = key
        self._ttl_seconds = ttl_seconds
        self._token = token or uuid.uuid4().hex
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    async def acquire(self) -> bool:
        acquired = await self._redis.set(
            self._key,
            self._token,
            nx=True,
            ex=self._ttl_seconds,
        )
        self._held = bool(acquired)
        return self._held

    async def release(self) -> None:
        if not self._held:
            return
        await self._redis.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
        self._held = False

    async def __aenter__(self) -> RedisDistributedLock:
        acquired = await self.acquire()
        if not acquired:
            raise LockNotAcquiredError(f"Could not acquire lock: {self._key}")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


class LockNotAcquiredError(RuntimeError):
    """Raised when a distributed lock cannot be acquired."""
