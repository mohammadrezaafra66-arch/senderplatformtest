"""Worker pool liveness heartbeats in Redis."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from workers.redis_keys import worker_heartbeat_key

if TYPE_CHECKING:
    from redis.asyncio import Redis


def resolve_worker_hostname() -> str:
    return os.environ.get("HOSTNAME", "whatsapp-worker-local")


async def publish_worker_heartbeat(
    redis: Redis,
    *,
    platform: str,
    hostname: str,
    assigned_account_ids: list[int],
    pool_size: int,
    pool_index: int,
    ttl_seconds: int,
) -> None:
    payload = {
        "platform": platform,
        "hostname": hostname,
        "assigned_account_ids": assigned_account_ids,
        "pool_size": pool_size,
        "pool_index": pool_index,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    key = worker_heartbeat_key(platform, hostname)
    await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=ttl_seconds)
