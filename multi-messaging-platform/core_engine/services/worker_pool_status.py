"""Read WhatsApp worker pool heartbeats from Redis."""

from __future__ import annotations

import json
from typing import Any

async def list_whatsapp_pool_workers(redis_client) -> list[dict[str, Any]]:
    """Return live whatsapp_worker_pool replicas reported via heartbeat keys."""
    workers: list[dict[str, Any]] = []
    cursor = 0
    pattern = "worker:alive:whatsapp:*"

    while True:
        cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=50)
        for key in keys:
            raw = await redis_client.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            hostname = str(payload.get("hostname") or key.rsplit(":", 1)[-1])
            assigned = payload.get("assigned_account_ids") or []
            if not isinstance(assigned, list):
                assigned = []

            workers.append(
                {
                    "hostname": hostname,
                    "pool_size": int(payload.get("pool_size") or 0),
                    "pool_index": int(payload.get("pool_index") or 0),
                    "assigned_account_ids": [int(item) for item in assigned],
                    "updated_at": payload.get("updated_at"),
                }
            )

        if cursor == 0:
            break

    workers.sort(key=lambda item: (item["pool_index"], item["hostname"]))
    return workers


def account_covered_by_pool(account_id: int, workers: list[dict[str, Any]]) -> bool:
    """True when a live pool replica lists this account in its assignment."""
    return any(account_id in worker.get("assigned_account_ids", []) for worker in workers)
