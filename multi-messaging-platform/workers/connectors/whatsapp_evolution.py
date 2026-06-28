"""کانکتور ارسال پیام واتساپ از طریق Evolution API."""

from __future__ import annotations

import os

import httpx

from core_engine.config import get_settings
from core_engine.database import SessionLocal
from core_engine.services.evolution_service import _get_instance_name
from workers.config import WorkerSettings
from workers.payloads import WorkerPayload, WorkerResult


def _evolution_credentials() -> tuple[str, str]:
    """Read Evolution base URL + API key (settings first, env fallback)."""
    app_settings = get_settings()
    base_url = (
        getattr(app_settings, "EVOLUTION_API_URL", None)
        or os.getenv("EVOLUTION_API_URL", "")
    ).rstrip("/")
    api_key = (
        getattr(app_settings, "EVOLUTION_API_KEY", None)
        or os.getenv("EVOLUTION_API_KEY", "")
    )
    return base_url, api_key


async def deliver_whatsapp_evolution_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """یک پیام متنی را از طریق Evolution API برای این اکانت ارسال کن."""
    db = SessionLocal()
    try:
        instance_name = _get_instance_name(db, int(payload.account_id))
    finally:
        db.close()

    if not instance_name:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            retryable=False,
            error_code="evolution_no_instance",
            error_message=f"No Evolution instance for account {payload.account_id}",
        )

    base_url, api_key = _evolution_credentials()
    recipient = str(payload.recipient).lstrip("+")
    url = f"{base_url}/message/sendText/{instance_name}"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {"number": recipient, "text": payload.message_text}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code in (200, 201):
            data = response.json()
            message_id = None
            if isinstance(data, dict) and isinstance(data.get("key"), dict):
                message_id = data["key"].get("id")
            return WorkerResult(
                success=True,
                status="sent",
                platform_message_id=message_id,
                retryable=False,
            )
        if response.status_code in (401, 403):
            return WorkerResult(
                success=False,
                status="failed_permanent",
                retryable=False,
                error_code="evolution_auth_error",
                error_message=f"Evolution auth failed: {response.status_code}",
            )
        return WorkerResult(
            success=False,
            status="failed_retryable",
            retryable=True,
            error_code="evolution_send_failed",
            error_message=f"Evolution returned {response.status_code}: {response.text[:200]}",
        )
    except httpx.TimeoutException:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            retryable=True,
            error_code="evolution_timeout",
            error_message="Evolution API timed out",
        )
    except Exception as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            retryable=True,
            error_code="evolution_exception",
            error_message=str(exc),
        )
