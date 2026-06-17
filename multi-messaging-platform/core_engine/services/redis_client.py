"""کلاینت Redis async با اتصال lazy."""

from __future__ import annotations

from redis.asyncio import Redis

from core_engine.config import get_settings

_redis_client: Redis | None = None

DASHBOARD_QUEUE_NAMES = (
    "queue:bale",
    "queue:telegram",
    "queue:whatsapp",
    "queue:rubika",
)


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )
    return _redis_client


def reset_redis_client() -> None:
    """Drop cached client so a later call can reconnect after failures."""
    global _redis_client
    _redis_client = None


async def ping_redis(*, max_attempts: int = 3) -> bool:
    for attempt in range(max_attempts):
        try:
            client = get_redis_client()
            if await client.ping():
                return True
        except Exception:
            if attempt + 1 >= max_attempts:
                reset_redis_client()
                return False
    return False


async def get_dashboard_queue_pending() -> tuple[list[dict[str, int | str]], bool]:
    """Read pending counts for standard dashboard queue names.

    Returns (queues, redis_connected). On failure every queue reports pending=0.
    """
    queues: list[dict[str, int | str]] = [
        {"name": name, "pending": 0} for name in DASHBOARD_QUEUE_NAMES
    ]
    try:
        client = get_redis_client()
        await client.ping()
        for item in queues:
            name = str(item["name"])
            length = await client.llen(name)
            item["pending"] = int(length) if length is not None else 0
        return queues, True
    except Exception:
        return queues, False

