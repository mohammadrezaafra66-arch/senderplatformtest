"""سرویس کنترل‌های عملیاتی داشبورد — Kill Switch و delay حساب در Redis."""

from __future__ import annotations

from fastapi import HTTPException

from core_engine.services.redis_client import get_redis_client, ping_redis

KILL_SWITCH_REDIS_KEY = "system:kill_switch"
DEFAULT_DELAY_SECONDS = 30
MIN_DELAY_SECONDS = 1
MAX_DELAY_SECONDS = 3600


class ControlServiceError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def delay_redis_key(account_id: int) -> str:
    return f"config:delay:{account_id}"


def _parse_redis_bool(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().lower() == "true"


async def _require_redis() -> None:
    if not await ping_redis():
        raise ControlServiceError("Redis is unavailable", status_code=503)


def _raise_control_error(exc: ControlServiceError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


async def get_controls_status() -> dict[str, object]:
    redis_available = await ping_redis()
    kill_switch_enabled = False
    if redis_available:
        try:
            kill_switch_enabled = (await get_kill_switch_status())["enabled"]
        except ControlServiceError:
            redis_available = False

    return {
        "kill_switch": {"enabled": kill_switch_enabled},
        "defaults": {"delay_seconds": DEFAULT_DELAY_SECONDS},
        "redis": {"available": redis_available},
    }


async def get_kill_switch_status() -> dict[str, object]:
    await _require_redis()
    client = get_redis_client()
    try:
        value = await client.get(KILL_SWITCH_REDIS_KEY)
    except Exception as exc:
        raise ControlServiceError("Redis is unavailable", status_code=503) from exc

    return {
        "enabled": _parse_redis_bool(value),
        "redis_key": KILL_SWITCH_REDIS_KEY,
        "updated_at": None,
    }


async def set_kill_switch(enabled: bool) -> dict[str, object]:
    await _require_redis()
    client = get_redis_client()
    try:
        await client.set(KILL_SWITCH_REDIS_KEY, "true" if enabled else "false")
    except Exception as exc:
        raise ControlServiceError("Failed to update kill switch in Redis", status_code=503) from exc

    return {
        "success": True,
        "enabled": enabled,
        "redis_key": KILL_SWITCH_REDIS_KEY,
    }


async def get_account_delay(account_id: int) -> dict[str, object]:
    await _require_redis()
    key = delay_redis_key(account_id)
    client = get_redis_client()
    try:
        value = await client.get(key)
    except Exception as exc:
        raise ControlServiceError("Redis is unavailable", status_code=503) from exc

    if value is None or str(value).strip() == "":
        return {
            "account_id": account_id,
            "delay_seconds": DEFAULT_DELAY_SECONDS,
            "redis_key": key,
            "source": "redis_or_default",
        }

    try:
        delay_seconds = int(str(value).strip())
    except ValueError as exc:
        raise ControlServiceError(
            f"Invalid delay value in Redis for account {account_id}",
            status_code=500,
        ) from exc

    return {
        "account_id": account_id,
        "delay_seconds": delay_seconds,
        "redis_key": key,
        "source": "redis",
    }


async def set_account_delay(account_id: int, delay_seconds: int) -> dict[str, object]:
    if delay_seconds < MIN_DELAY_SECONDS or delay_seconds > MAX_DELAY_SECONDS:
        raise HTTPException(
            status_code=422,
            detail=f"delay_seconds must be between {MIN_DELAY_SECONDS} and {MAX_DELAY_SECONDS}",
        )

    await _require_redis()
    key = delay_redis_key(account_id)
    client = get_redis_client()
    try:
        await client.set(key, str(delay_seconds))
    except Exception as exc:
        raise ControlServiceError("Failed to update account delay in Redis", status_code=503) from exc

    return {
        "success": True,
        "account_id": account_id,
        "delay_seconds": delay_seconds,
        "redis_key": key,
    }


async def get_kill_switch_enabled_for_snapshot() -> bool:
    """Lightweight read for dashboard snapshot; returns False if Redis is down."""
    try:
        if not await ping_redis():
            return False
        status = await get_kill_switch_status()
        return bool(status["enabled"])
    except Exception:
        return False
