"""Account list parsing and horizontal sharding for multi-account worker pools."""

from __future__ import annotations

import os
import zlib


def parse_account_id_list(raw: str) -> list[int]:
    """Parse a comma-separated list of positive account IDs."""
    text = (raw or "").strip()
    if not text:
        return []

    account_ids: list[int] = []
    seen: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"Invalid account id '{token}' in account list.")
        account_id = int(token)
        if account_id <= 0:
            raise ValueError(f"Account id must be positive, got {account_id}.")
        if account_id in seen:
            continue
        seen.add(account_id)
        account_ids.append(account_id)

    account_ids.sort()
    return account_ids


def shard_account_ids(
    account_ids: list[int],
    *,
    pool_size: int,
    pool_index: int,
) -> list[int]:
    """Assign accounts to a pool replica using stable account_id modulo sharding."""
    if pool_size < 1:
        raise ValueError("pool_size must be >= 1.")
    if pool_index < 0 or pool_index >= pool_size:
        raise ValueError(f"pool_index must be in [0, {pool_size - 1}], got {pool_index}.")

    return [
        account_id
        for account_id in sorted(account_ids)
        if account_belongs_to_shard(account_id, pool_size=pool_size, pool_index=pool_index)
    ]


def account_belongs_to_shard(account_id: int, *, pool_size: int, pool_index: int) -> bool:
    """Return True when an account is owned by the given pool replica."""
    if pool_size < 1:
        raise ValueError("pool_size must be >= 1.")
    if pool_index < 0 or pool_index >= pool_size:
        raise ValueError(f"pool_index must be in [0, {pool_size - 1}], got {pool_index}.")
    return (account_id % pool_size) == pool_index


def resolve_pool_index(*, pool_size: int, explicit_index: int) -> int:
    """Resolve the replica index for this process.

    When explicit_index is -1, derive a stable index from HOSTNAME so
    `docker compose up --scale whatsapp_worker_pool=N` works without manual env.
    """
    if pool_size < 1:
        raise ValueError("pool_size must be >= 1.")
    if explicit_index >= 0:
        return explicit_index % pool_size

    hostname = os.environ.get("HOSTNAME", "whatsapp-worker-0")
    digest = zlib.crc32(hostname.encode("utf-8")) & 0xFFFFFFFF
    return digest % pool_size


def resolve_assigned_account_ids(
    *,
    account_ids_raw: str,
    pool_size: int,
    pool_index: int,
    fallback_account_id: int,
) -> list[int]:
    """Build the account list owned by this worker replica."""
    parsed = parse_account_id_list(account_ids_raw)
    if not parsed:
        if fallback_account_id <= 0:
            raise ValueError("No account ids configured for worker pool.")
        parsed = [fallback_account_id]

    resolved_index = resolve_pool_index(pool_size=pool_size, explicit_index=pool_index)
    assigned = shard_account_ids(parsed, pool_size=pool_size, pool_index=resolved_index)
    if not assigned:
        raise ValueError(
            f"No accounts assigned to pool replica index {resolved_index} "
            f"(pool_size={pool_size}, total_accounts={len(parsed)})."
        )
    return assigned
