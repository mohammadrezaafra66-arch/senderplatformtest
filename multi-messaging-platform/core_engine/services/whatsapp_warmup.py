"""Trigger cross-account WhatsApp warmup via the Node.js mini-API."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from core_engine.config import get_settings
from core_engine.services.redis_client import get_redis_client

WARMUP_LOCK_KEY = "whatsapp:warmup_lock"
WARMUP_LOCK_TTL_SECONDS = 86400
WARMUP_LOCK_VALUE = "locked"


async def trigger_whatsapp_warmup() -> dict[str, Any]:
    """Acquire 24h debounce lock, call Node /api/warmup, rollback lock on failure."""
    redis = get_redis_client()

    if await redis.exists(WARMUP_LOCK_KEY):
        raise HTTPException(
            status_code=429,
            detail="گرم‌سازی در ۲۴ ساعت گذشته برنامه‌ریزی شده است. لطفاً فردا تلاش کنید.",
        )

    await redis.setex(WARMUP_LOCK_KEY, WARMUP_LOCK_TTL_SECONDS, WARMUP_LOCK_VALUE)

    settings = get_settings()
    warmup_url = f"{settings.WHATSAPP_SERVICE_URL.rstrip('/')}/api/warmup"
    headers: dict[str, str] = {}
    api_key = (settings.WHATSAPP_SERVICE_API_KEY or "").strip()
    if api_key:
        headers["X-Api-Key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(warmup_url, headers=headers)
    except httpx.HTTPError as exc:
        await redis.delete(WARMUP_LOCK_KEY)
        raise HTTPException(
            status_code=503,
            detail=f"WhatsApp microservice unreachable: {exc}",
        ) from exc

    if response.is_success:
        return response.json()

    await redis.delete(WARMUP_LOCK_KEY)

    try:
        detail: Any = response.json()
    except ValueError:
        detail = response.text or "WhatsApp warmup request failed"

    raise HTTPException(status_code=response.status_code, detail=detail)
