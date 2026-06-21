"""WhatsApp Web send kill switch — env flag + Redis runtime toggle."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from core_engine.config import get_settings

WHATSAPP_SEND_KILL_REDIS_KEY = "system:whatsapp_send_disabled"

if TYPE_CHECKING:
    from redis.asyncio import Redis


class WhatsAppSendBlockedError(Exception):
    """Raised when WhatsApp sending is disabled by env or kill switch."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def whatsapp_sending_disabled_by_env() -> bool:
    """True when WHATSAPP_SENDING_DISABLED is set in environment or Settings."""
    env_raw = os.environ.get("WHATSAPP_SENDING_DISABLED", "").strip().lower()
    if env_raw in {"1", "true", "yes", "on"}:
        return True
    try:
        return bool(get_settings().WHATSAPP_SENDING_DISABLED)
    except Exception:
        return False


def _parse_redis_bool(value: str | bytes | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def whatsapp_send_kill_switch_enabled(redis: Redis | None = None) -> bool:
    """True when Redis runtime kill switch is on."""
    if redis is not None:
        try:
            value = await redis.get(WHATSAPP_SEND_KILL_REDIS_KEY)
            return _parse_redis_bool(value)
        except Exception:
            return False

    from core_engine.services.redis_client import get_redis_client, ping_redis

    if not await ping_redis():
        return False
    client = get_redis_client()
    try:
        value = await client.get(WHATSAPP_SEND_KILL_REDIS_KEY)
        return _parse_redis_bool(value)
    except Exception:
        return False


async def assert_whatsapp_send_allowed(*, redis: Redis | None = None) -> None:
    """Raise WhatsAppSendBlockedError when sending must not proceed."""
    if whatsapp_sending_disabled_by_env():
        raise WhatsAppSendBlockedError(
            "WHATSAPP_SENDING_DISABLED is true — all WhatsApp Web sends are blocked."
        )
    if await whatsapp_send_kill_switch_enabled(redis):
        raise WhatsAppSendBlockedError(
            f"WhatsApp send kill switch is ON (Redis key {WHATSAPP_SEND_KILL_REDIS_KEY})."
        )


async def set_whatsapp_send_kill_switch(enabled: bool, *, redis: Redis | None = None) -> dict:
    """Enable or disable the Redis runtime WhatsApp send kill switch."""
    value = "true" if enabled else "false"
    if redis is not None:
        await redis.set(WHATSAPP_SEND_KILL_REDIS_KEY, value)
        await redis.set("whatsapp:kill_switch", value)
        return {"enabled": enabled, "redis_key": WHATSAPP_SEND_KILL_REDIS_KEY}

    from core_engine.services.redis_client import get_redis_client, ping_redis

    if not await ping_redis():
        raise RuntimeError("Redis is unavailable")
    client = get_redis_client()
    await client.set(WHATSAPP_SEND_KILL_REDIS_KEY, value)
    await client.set("whatsapp:kill_switch", value)
    return {"enabled": enabled, "redis_key": WHATSAPP_SEND_KILL_REDIS_KEY}


async def whatsapp_send_guard_status(*, redis: Redis | None = None) -> dict:
    """Snapshot for API / preflight."""
    env_blocked = whatsapp_sending_disabled_by_env()
    redis_blocked = await whatsapp_send_kill_switch_enabled(redis)
    return {
        "env_disabled": env_blocked,
        "redis_kill_switch_enabled": redis_blocked,
        "sending_allowed": not env_blocked and not redis_blocked,
        "redis_key": WHATSAPP_SEND_KILL_REDIS_KEY,
    }


def whatsapp_send_kill_switch_enabled_sync() -> bool:
    """Sync Redis read for preflight checks (no async context)."""
    if _parse_redis_bool(os.environ.get("WHATSAPP_SENDING_DISABLED")):
        return True
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    try:
        import redis

        client = redis.from_url(redis_url, decode_responses=True)
        value = client.get(WHATSAPP_SEND_KILL_REDIS_KEY)
        client.close()
        return _parse_redis_bool(value)
    except Exception:
        return False


def whatsapp_send_allowed_sync() -> tuple[bool, str]:
    """Return (allowed, reason) for sync preflight."""
    if whatsapp_sending_disabled_by_env():
        return False, "WHATSAPP_SENDING_DISABLED is true."
    if whatsapp_send_kill_switch_enabled_sync():
        return False, f"WhatsApp send kill switch ON ({WHATSAPP_SEND_KILL_REDIS_KEY})."
    return True, "WhatsApp sending is allowed."
