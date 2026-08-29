"""Centralized Redis control-flag contract (pause / kill / truthy parse).

Writers always store canonical ``\"true\"`` / ``\"false\"``.
Readers accept ``true`` / ``1`` / ``yes`` / ``on`` (case-insensitive).
"""

from __future__ import annotations

from typing import Any

from workers.redis_keys import account_pause_key, campaign_pause_key, kill_switch_key

REDIS_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
REDIS_FLAG_TRUE = "true"
REDIS_FLAG_FALSE = "false"


def parse_redis_truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value).strip().lower() in REDIS_TRUTHY_VALUES


def encode_redis_flag(enabled: bool) -> str:
    return REDIS_FLAG_TRUE if enabled else REDIS_FLAG_FALSE


async def set_account_pause(redis: Any, account_id: int | str, *, paused: bool = True) -> None:
    key = account_pause_key(account_id)
    if paused:
        await redis.set(key, REDIS_FLAG_TRUE)
    else:
        await redis.delete(key)


async def clear_account_pause(redis: Any, account_id: int | str) -> None:
    await set_account_pause(redis, account_id, paused=False)


async def is_account_paused(redis: Any, account_id: int | str) -> bool:
    return parse_redis_truthy(await redis.get(account_pause_key(account_id)))


async def set_campaign_pause_flag(
    redis: Any, campaign_id: int | str, *, paused: bool = True
) -> None:
    key = campaign_pause_key(campaign_id)
    if paused:
        await redis.set(key, REDIS_FLAG_TRUE)
    else:
        await redis.delete(key)


async def clear_campaign_pause_flag(redis: Any, campaign_id: int | str) -> None:
    await set_campaign_pause_flag(redis, campaign_id, paused=False)


async def is_campaign_paused(redis: Any, campaign_id: int | str) -> bool:
    return parse_redis_truthy(await redis.get(campaign_pause_key(campaign_id)))


async def set_system_kill_switch(redis: Any, *, enabled: bool) -> None:
    await redis.set(kill_switch_key(), encode_redis_flag(enabled))


async def is_system_kill_switch_enabled(redis: Any) -> bool:
    return parse_redis_truthy(await redis.get(kill_switch_key()))
