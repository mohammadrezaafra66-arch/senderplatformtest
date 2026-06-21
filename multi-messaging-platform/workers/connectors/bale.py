"""Live Bale Bot API connector (https://tapi.bale.ai)."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from core_engine.models import SessionType
from workers.config import WorkerSettings
from workers.db import get_db_session
from workers.errors import PermanentWorkerError, RetryableWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult
from workers.session_access import load_account_session_plaintext

logger = logging.getLogger("workers.connectors.bale")

_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_ERROR_CODES = {429, 500}


def parse_bale_bot_token(plaintext: bytes) -> str:
    """Parse bot token from encrypted session plaintext (raw or JSON)."""
    text = plaintext.decode("utf-8").strip()
    if not text:
        raise SessionInvalidError("Bale bot token is empty.")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionInvalidError("Bale session JSON is invalid.") from exc
        if not isinstance(data, dict):
            raise SessionInvalidError("Bale session JSON must be an object.")
        for key in ("bot_token", "token", "api_token"):
            value = data.get(key)
            if value and str(value).strip():
                return str(value).strip()
        raise SessionInvalidError("Bale session JSON is missing bot_token.")

    return text


def resolve_bale_chat_id(payload: WorkerPayload) -> int | str:
    """Resolve Bale chat_id from worker payload metadata or recipient."""
    metadata = payload.metadata or {}
    for key in ("chat_id", "bale_chat_id", "user_id"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return _coerce_chat_id(value)

    channel_handle = metadata.get("channel_handle")
    if channel_handle and str(channel_handle).strip():
        return _coerce_chat_id(channel_handle)

    if payload.recipient_type == "channel_handle" and str(payload.recipient).strip():
        return _coerce_chat_id(payload.recipient)

    # Numeric recipient may already be a Bale user id stored as phone.
    if str(payload.recipient).strip().isdigit():
        return _coerce_chat_id(payload.recipient)

    raise PermanentWorkerError(
        "Bale Bot API requires chat_id (channel_handle), not a phone number.",
    )


def _coerce_chat_id(value: object) -> int | str:
    text = str(value).strip()
    if not text:
        raise PermanentWorkerError("Bale chat_id is empty.")
    if text.lstrip("-").isdigit():
        return int(text)
    return text


def _build_send_message_url(api_base_url: str, bot_token: str) -> str:
    base = api_base_url.rstrip("/")
    return f"{base}/bot{bot_token}/sendMessage"


async def request_bale_api(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.request(method, url, json=json_body)


def _parse_bale_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RetryableWorkerError(
            f"Bale API returned non-JSON response (HTTP {response.status_code}).",
            error_code="bale_bad_response",
        ) from exc

    if not isinstance(data, dict):
        raise RetryableWorkerError(
            "Bale API JSON body must be an object.",
            error_code="bale_bad_response",
        )
    return data


def _result_from_bale_response(data: dict[str, Any]) -> WorkerResult:
    if data.get("ok") is True:
        result = data.get("result") or {}
        message_id = None
        if isinstance(result, dict):
            message_id = result.get("message_id")
        platform_message_id = (
            f"bale-{message_id}" if message_id is not None else "bale-sent"
        )
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=str(platform_message_id),
            retryable=False,
        )

    description = str(data.get("description") or "Bale API error")
    error_code_raw = data.get("error_code")
    try:
        error_code_int = int(error_code_raw)
    except (TypeError, ValueError):
        error_code_int = None

    if error_code_int in _RETRYABLE_ERROR_CODES:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="bale_rate_limited" if error_code_int == 429 else "bale_api_error",
            error_message=description,
            retryable=True,
        )

    if error_code_int in (401, 403):
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="bale_unauthorized",
            error_message=description,
            retryable=False,
        )

    return WorkerResult(
        success=False,
        status="failed_permanent",
        error_code="bale_api_error",
        error_message=description,
        retryable=False,
    )


async def send_bale_text_message(
    *,
    bot_token: str,
    chat_id: int | str,
    text: str,
    settings: WorkerSettings,
) -> WorkerResult:
    url = _build_send_message_url(settings.BALE_API_BASE_URL, bot_token)
    body = {"chat_id": chat_id, "text": text}

    try:
        response = await request_bale_api(
            "POST",
            url,
            json_body=body,
            timeout_seconds=settings.BALE_API_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="bale_timeout",
            error_message=str(exc),
            retryable=True,
        )
    except httpx.HTTPError as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="bale_transport_error",
            error_message=str(exc),
            retryable=True,
        )

    if response.status_code in _RETRYABLE_HTTP_STATUS:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="bale_http_error",
            error_message=f"HTTP {response.status_code}",
            retryable=True,
        )

    if response.status_code >= 400 and response.status_code not in _RETRYABLE_HTTP_STATUS:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="bale_http_error",
            error_message=f"HTTP {response.status_code}",
            retryable=False,
        )

    data = _parse_bale_response(response)
    return _result_from_bale_response(data)


def load_bale_bot_token(account_id: int | str, db=None) -> str:
    owns_session = db is None
    session = db or get_db_session()
    try:
        plaintext = load_account_session_plaintext(
            session,
            account_id=int(account_id),
            session_type=SessionType.API_TOKEN,
        )
        return parse_bale_bot_token(plaintext)
    finally:
        if owns_session:
            session.close()


async def deliver_bale_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send a message through the Bale Bot API using the account session token."""
    try:
        bot_token = load_bale_bot_token(payload.account_id)
        chat_id = resolve_bale_chat_id(payload)
    except SessionInvalidError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="bale_session_missing",
            error_message=str(exc),
            retryable=False,
        )
    except PermanentWorkerError as exc:
        message = str(exc)
        error_code = (
            "bale_chat_id_required"
            if "chat_id" in message.lower()
            else "bale_invalid_payload"
        )
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=error_code,
            error_message=message,
            retryable=False,
        )

    return await send_bale_text_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=payload.message_text,
        settings=settings,
    )
