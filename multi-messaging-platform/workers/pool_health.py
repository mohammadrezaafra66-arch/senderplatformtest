"""Worker pool liveness heartbeats in Redis."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from workers.redis_keys import worker_heartbeat_key

# Heartbeat contract (Rubika and shared coverage publisher):
# - worker:alive:{platform}:{hostname} TTL = WORKER_HEARTBEAT_TTL_SECONDS (default 45)
#   refreshed every WORKER_HEARTBEAT_INTERVAL_SECONDS (default 15)
#   payload.assigned_account_ids is the only account binding on the worker key
# - worker:coverage:{platform}:{account_id} TTL = heartbeat TTL
#   payload.account_id must match the key; a worker for 12 must not write 13
# - worker:coverage:last:{platform}:{account_id} TTL = 24h so expiry is "stale"
#   rather than "never seen". Missing both keys = no worker, not a disconnect.
WORKER_COVERAGE_LAST_SEEN_TTL_SECONDS = 86400

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


async def publish_account_coverage(
    redis: Redis,
    *,
    platform: str,
    account_ids: list[int],
    hostname: str,
    ttl_seconds: int,
) -> None:
    """Publish TTL-backed per-account coverage (real runtime ownership).

    Coverage is written only for the account ids the caller passes. Publishing
    account 12 never creates a coverage key for account 13.
    """
    from workers.redis_keys import worker_account_coverage_key, worker_account_coverage_last_key

    now = datetime.now(timezone.utc).isoformat()
    for account_id in account_ids:
        payload = {
            "platform": platform,
            "account_id": int(account_id),
            "hostname": hostname,
            "updated_at": now,
        }
        body = json.dumps(payload, ensure_ascii=False)
        await redis.set(
            worker_account_coverage_key(platform, account_id),
            body,
            ex=ttl_seconds,
        )
        await redis.set(
            worker_account_coverage_last_key(platform, account_id),
            body,
            ex=WORKER_COVERAGE_LAST_SEEN_TTL_SECONDS,
        )


async def has_active_worker_coverage(
    redis: Redis,
    *,
    platform: str,
    account_id: int,
) -> bool:
    from workers.redis_keys import worker_account_coverage_key

    return bool(await redis.exists(worker_account_coverage_key(platform, account_id)))
