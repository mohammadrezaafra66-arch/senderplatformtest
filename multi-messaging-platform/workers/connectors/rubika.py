"""Live Rubika Bot API connector (https://botapi.rubika.ir/v3).

Docs: https://rubika.ir/botapi
Endpoint pattern: POST https://botapi.rubika.ir/v3/{token}/sendMessage
"""

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

logger = logging.getLogger("workers.connectors.rubika")

_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503}
_PERMANENT_STATUS_CODES = {400, 401, 403, 404}


def parse_rubika_bot_token(plaintext: bytes) -> str:
    """Parse bot token from encrypted session plaintext (raw or JSON)."""
    text = plaintext.decode("utf-8").strip()
    if not text:
        raise SessionInvalidError("Rubika bot token is empty.")

    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SessionInvalidError("Rubika session JSON is invalid.") from exc
        if not isinstance(data, dict):
            raise SessionInvalidError("Rubika session JSON must be an object.")
        for key in ("bot_token", "token", "api_token"):
            value = data.get(key)
            if value and str(value).strip():
                return str(value).strip()
        raise SessionInvalidError("Rubika session JSON is missing bot_token.")

    return text


def resolve_rubika_chat_id(payload: WorkerPayload) -> str:
    """Resolve Rubika chat_id from worker payload metadata or recipient."""
    metadata = payload.metadata or {}
    for key in ("chat_id", "rubika_chat_id", "user_id"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return _normalize_chat_id(value)

    channel_handle = metadata.get("channel_handle")
    if channel_handle and str(channel_handle).strip():
        return _normalize_chat_id(channel_handle)

    if payload.recipient_type == "channel_handle" and str(payload.recipient).strip():
        return _normalize_chat_id(payload.recipient)

    if str(payload.recipient).strip():
        return _normalize_chat_id(payload.recipient)

    raise PermanentWorkerError(
        "Rubika Bot API requires chat_id (channel_handle), not a phone number.",
    )


def _normalize_chat_id(value: object) -> str:
    chat_id = str(value).strip()
    if not chat_id:
        raise PermanentWorkerError("Rubika chat_id is empty.")
    return chat_id


def _build_method_url(api_base_url: str, bot_token: str, method: str) -> str:
    base = api_base_url.rstrip("/")
    token = bot_token.strip("/")
    method_name = method.strip("/")
    return f"{base}/{token}/{method_name}"


async def request_rubika_api(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.request(method, url, json=json_body)


def _parse_rubika_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RetryableWorkerError(
            f"Rubika API returned non-JSON response (HTTP {response.status_code}).",
            error_code="rubika_bad_response",
        ) from exc

    if not isinstance(data, dict):
        raise RetryableWorkerError(
            "Rubika API JSON body must be an object.",
            error_code="rubika_bad_response",
        )
    return data


def _response_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    return data


def _extract_message_id(data: dict[str, Any]) -> str | None:
    payload = _response_payload(data)
    message_id = payload.get("message_id")
    if message_id is None:
        return None
    text = str(message_id).strip()
    return text or None


def _result_from_rubika_response(data: dict[str, Any]) -> WorkerResult:
    message_id = _extract_message_id(data)
    status_text = str(data.get("status") or "").strip().upper()

    if message_id and status_text in {"", "OK", "DONE", "SUCCESS"}:
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"rubika-{message_id}",
            retryable=False,
        )

    if message_id and status_text not in {"ERROR", "FAILED"}:
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id=f"rubika-{message_id}",
            retryable=False,
        )

    description = str(
        data.get("dev_message")
        or data.get("message")
        or data.get("description")
        or "Rubika API error",
    )
    code_raw = data.get("code") or data.get("status_code") or data.get("error_code")
    try:
        code_int = int(code_raw)
    except (TypeError, ValueError):
        code_int = None

    if status_text in {"ERROR", "FAILED"} or (code_int is not None and code_int >= 400):
        if code_int in _RETRYABLE_STATUS_CODES or code_int == 429:
            return WorkerResult(
                success=False,
                status="failed_retryable",
                error_code="rubika_rate_limited" if code_int == 429 else "rubika_api_error",
                error_message=description,
                retryable=True,
            )
        if code_int in _PERMANENT_STATUS_CODES or code_int in (401, 403):
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="rubika_unauthorized",
                error_message=description,
                retryable=False,
            )
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="rubika_api_error",
            error_message=description,
            retryable=False,
        )

    if status_text in {"OK", "DONE", "SUCCESS"}:
        return WorkerResult(
            success=True,
            status="delivered",
            platform_message_id="rubika-sent",
            retryable=False,
        )

    return WorkerResult(
        success=False,
        status="failed_permanent",
        error_code="rubika_api_error",
        error_message=description,
        retryable=False,
    )


async def send_rubika_text_message(
    *,
    bot_token: str,
    chat_id: str,
    text: str,
    settings: WorkerSettings,
) -> WorkerResult:
    url = _build_method_url(settings.RUBIKA_API_BASE_URL, bot_token, "sendMessage")
    body = {"chat_id": chat_id, "text": text}

    try:
        response = await request_rubika_api(
            "POST",
            url,
            json_body=body,
            timeout_seconds=settings.RUBIKA_API_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="rubika_timeout",
            error_message=str(exc),
            retryable=True,
        )
    except httpx.HTTPError as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="rubika_transport_error",
            error_message=str(exc),
            retryable=True,
        )

    if response.status_code in _RETRYABLE_HTTP_STATUS:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="rubika_http_error",
            error_message=f"HTTP {response.status_code}",
            retryable=True,
        )

    if response.status_code >= 400 and response.status_code not in _RETRYABLE_HTTP_STATUS:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="rubika_http_error",
            error_message=f"HTTP {response.status_code}",
            retryable=False,
        )

    data = _parse_rubika_response(response)
    return _result_from_rubika_response(data)


def load_rubika_bot_token(account_id: int | str, db=None) -> str:
    owns_session = db is None
    session = db or get_db_session()
    try:
        plaintext = load_account_session_plaintext(
            session,
            account_id=int(account_id),
            session_type=SessionType.API_TOKEN,
        )
        return parse_rubika_bot_token(plaintext)
    finally:
        if owns_session:
            session.close()


async def deliver_rubika_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send a message through the Rubika Bot API using the account session token."""
    try:
        bot_token = load_rubika_bot_token(payload.account_id)
        chat_id = resolve_rubika_chat_id(payload)
    except SessionInvalidError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="rubika_session_missing",
            error_message=str(exc),
            retryable=False,
        )
    except PermanentWorkerError as exc:
        message = str(exc)
        error_code = (
            "rubika_chat_id_required"
            if "chat_id" in message.lower()
            else "rubika_invalid_payload"
        )
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=error_code,
            error_message=message,
            retryable=False,
        )

    logger.info(
        "rubika_send_attempt account_id=%s chat_id_suffix=%s",
        payload.account_id,
        chat_id[-4:] if len(chat_id) >= 4 else "****",
    )

    return await send_rubika_text_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=payload.message_text,
        settings=settings,
    )
