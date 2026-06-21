"""Live WhatsApp Cloud API connector (Meta Graph API).

Expects encrypted session JSON in channel_sessions (session_type=api_token):
    {
      "access_token": "<permanent or long-lived token>",
      "phone_number_id": "<WhatsApp business phone number id>"
    }

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from core_engine.models import SessionType
from core_engine.services.phase4_utils import normalize_phone
from workers.config import WorkerSettings
from workers.db import get_db_session
from workers.errors import PermanentWorkerError, RetryableWorkerError, SessionInvalidError
from workers.payloads import WorkerPayload, WorkerResult
from workers.session_access import load_account_session_plaintext

logger = logging.getLogger("workers.connectors.whatsapp")

_RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}
_RETRYABLE_META_ERROR_CODES = frozenset({
    2,        # Service temporarily unavailable
    368,      # Temporarily blocked for policies
    130429,   # Throughput / rate limit
    80007,    # Rate limit issues
    131056,   # Pair rate limit hit
})
_PERMANENT_META_ERROR_CODES = frozenset({
    100,      # Invalid parameter
    190,      # Invalid OAuth access token
    131026,   # Receiver is incapable of receiving
    131047,   # Re-engagement message required (24h window)
    131051,   # Unsupported message type
    133010,   # Phone number not registered on WhatsApp
    133006,   # Phone number quality / restriction
})


@dataclass(frozen=True, slots=True)
class WhatsAppCredentials:
    access_token: str
    phone_number_id: str


def parse_whatsapp_credentials(plaintext: bytes) -> WhatsAppCredentials:
    """Parse Cloud API credentials from encrypted session plaintext."""
    text = plaintext.decode("utf-8").strip()
    if not text:
        raise SessionInvalidError("WhatsApp session credentials are empty.")

    if not text.startswith("{"):
        raise SessionInvalidError(
            "WhatsApp session must be JSON with access_token and phone_number_id."
        )

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SessionInvalidError("WhatsApp session JSON is invalid.") from exc

    if not isinstance(data, dict):
        raise SessionInvalidError("WhatsApp session JSON must be an object.")

    access_token = _first_non_empty(
        data,
        "access_token",
        "api_token",
        "token",
        "bearer_token",
    )
    phone_number_id = _first_non_empty(
        data,
        "phone_number_id",
        "phone_id",
        "from_phone_number_id",
    )

    if not access_token:
        raise SessionInvalidError("WhatsApp session JSON is missing access_token.")
    if not phone_number_id:
        raise SessionInvalidError("WhatsApp session JSON is missing phone_number_id.")
    if not re.fullmatch(r"\d+", phone_number_id):
        raise SessionInvalidError("WhatsApp phone_number_id must be numeric.")

    return WhatsAppCredentials(
        access_token=access_token,
        phone_number_id=phone_number_id,
    )


def _first_non_empty(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def to_whatsapp_recipient_e164_digits(phone: str) -> str:
    """Convert a phone string to WhatsApp Cloud API `to` format (E.164 without '+')."""
    try:
        normalized = normalize_phone(phone)
    except ValueError as exc:
        raise PermanentWorkerError(
            f"WhatsApp recipient phone is invalid: {exc}",
        ) from exc
    digits = normalized.lstrip("+")
    if not digits or not digits.isdigit():
        raise PermanentWorkerError("WhatsApp recipient phone has no valid digits.")
    if len(digits) < 8 or len(digits) > 15:
        raise PermanentWorkerError(
            "WhatsApp recipient phone must be 8–15 digits in E.164 form."
        )
    return digits


def resolve_whatsapp_recipient(payload: WorkerPayload) -> str:
    """Resolve WhatsApp `to` field from worker payload."""
    metadata = payload.metadata or {}

    for key in ("whatsapp_to", "wa_id", "to"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            raw = str(value).strip()
            if raw.isdigit():
                return raw
            return to_whatsapp_recipient_e164_digits(raw)

    if payload.recipient_type == "phone_number" and str(payload.recipient).strip():
        return to_whatsapp_recipient_e164_digits(payload.recipient)

    phone = metadata.get("phone")
    if phone and str(phone).strip():
        return to_whatsapp_recipient_e164_digits(str(phone))

    raise PermanentWorkerError(
        "WhatsApp Cloud API requires a valid phone_number recipient.",
    )


def _build_messages_url(api_base_url: str, phone_number_id: str) -> str:
    base = api_base_url.rstrip("/")
    return f"{base}/{phone_number_id}/messages"


def _build_cloud_api_body(recipient: str, text: str) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": text,
        },
    }


async def request_whatsapp_api(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float,
) -> httpx.Response:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.request(method, url, headers=headers, json=json_body)


def _parse_whatsapp_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise RetryableWorkerError(
            f"WhatsApp API returned non-JSON response (HTTP {response.status_code}).",
        ) from exc

    if not isinstance(data, dict):
        raise RetryableWorkerError(
            "WhatsApp API JSON body must be an object.",
        )
    return data


def _result_from_whatsapp_success(data: dict[str, Any]) -> WorkerResult:
    messages = data.get("messages")
    message_id = None
    if isinstance(messages, list) and messages:
        first = messages[0]
        if isinstance(first, dict):
            message_id = first.get("id")

    platform_message_id = (
        str(message_id) if message_id else "whatsapp-sent"
    )
    return WorkerResult(
        success=True,
        status="delivered",
        platform_message_id=platform_message_id,
        retryable=False,
    )


def _classify_meta_error(error: dict[str, Any]) -> WorkerResult:
    message = str(error.get("message") or "WhatsApp API error")
    error_type = str(error.get("type") or "")
    try:
        code = int(error.get("code"))
    except (TypeError, ValueError):
        code = None

    if code in _RETRYABLE_META_ERROR_CODES:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_rate_limited" if code in {130429, 80007, 131056} else "whatsapp_api_error",
            error_message=f"{message} (type={error_type}, code={code})",
            retryable=True,
        )

    if code in _PERMANENT_META_ERROR_CODES or code in (401, 403):
        error_code = "whatsapp_unauthorized" if code == 190 else "whatsapp_api_error"
        if code == 131047:
            error_code = "whatsapp_reengagement_required"
        elif code == 131026:
            error_code = "whatsapp_undeliverable"
        elif code == 133010:
            error_code = "whatsapp_not_registered"
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=error_code,
            error_message=f"{message} (type={error_type}, code={code})",
            retryable=False,
        )

    if code == 429:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_rate_limited",
            error_message=message,
            retryable=True,
        )

    return WorkerResult(
        success=False,
        status="failed_permanent",
        error_code="whatsapp_api_error",
        error_message=f"{message} (type={error_type}, code={code})",
        retryable=False,
    )


def _result_from_whatsapp_response(data: dict[str, Any]) -> WorkerResult:
    if data.get("messages"):
        return _result_from_whatsapp_success(data)

    error = data.get("error")
    if isinstance(error, dict):
        return _classify_meta_error(error)

    return WorkerResult(
        success=False,
        status="failed_permanent",
        error_code="whatsapp_api_error",
        error_message="WhatsApp API response missing messages and error.",
        retryable=False,
    )


async def send_whatsapp_text_message(
    *,
    credentials: WhatsAppCredentials,
    recipient: str,
    text: str,
    settings: WorkerSettings,
) -> WorkerResult:
    if not str(text).strip():
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_empty_message",
            error_message="message_text is empty.",
            retryable=False,
        )

    url = _build_messages_url(settings.WHATSAPP_API_BASE_URL, credentials.phone_number_id)
    headers = {
        "Authorization": f"Bearer {credentials.access_token}",
        "Content-Type": "application/json",
    }
    body = _build_cloud_api_body(recipient, text)

    try:
        response = await request_whatsapp_api(
            "POST",
            url,
            headers=headers,
            json_body=body,
            timeout_seconds=settings.WHATSAPP_API_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_timeout",
            error_message=str(exc),
            retryable=True,
        )
    except httpx.HTTPError as exc:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_transport_error",
            error_message=str(exc),
            retryable=True,
        )

    if response.status_code in _RETRYABLE_HTTP_STATUS:
        return WorkerResult(
            success=False,
            status="failed_retryable",
            error_code="whatsapp_http_error",
            error_message=f"HTTP {response.status_code}",
            retryable=True,
        )

    if response.status_code == 401:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_unauthorized",
            error_message="HTTP 401 — invalid or expired access token.",
            retryable=False,
        )

    if response.status_code >= 400 and response.status_code not in _RETRYABLE_HTTP_STATUS:
        try:
            data = _parse_whatsapp_response(response)
            return _result_from_whatsapp_response(data)
        except RetryableWorkerError:
            return WorkerResult(
                success=False,
                status="failed_permanent",
                error_code="whatsapp_http_error",
                error_message=f"HTTP {response.status_code}",
                retryable=False,
            )

    data = _parse_whatsapp_response(response)
    return _result_from_whatsapp_response(data)


def load_whatsapp_credentials(account_id: int | str, db=None) -> WhatsAppCredentials:
    owns_session = db is None
    session = db or get_db_session()
    try:
        plaintext = load_account_session_plaintext(
            session,
            account_id=int(account_id),
            session_type=SessionType.API_TOKEN,
        )
        return parse_whatsapp_credentials(plaintext)
    finally:
        if owns_session:
            session.close()


async def deliver_whatsapp_cloud_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Send a text message through WhatsApp Cloud API (Meta Graph API)."""
    try:
        credentials = load_whatsapp_credentials(payload.account_id)
        recipient = resolve_whatsapp_recipient(payload)
    except SessionInvalidError as exc:
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code="whatsapp_session_missing",
            error_message=str(exc),
            retryable=False,
        )
    except PermanentWorkerError as exc:
        message = str(exc)
        error_code = (
            "whatsapp_recipient_invalid"
            if "recipient" in message.lower() or "phone" in message.lower()
            else "whatsapp_invalid_payload"
        )
        return WorkerResult(
            success=False,
            status="failed_permanent",
            error_code=error_code,
            error_message=message,
            retryable=False,
        )

    logger.info(
        "whatsapp_send_attempt account_id=%s recipient_suffix=%s",
        payload.account_id,
        recipient[-4:] if len(recipient) >= 4 else "****",
    )

    return await send_whatsapp_text_message(
        credentials=credentials,
        recipient=recipient,
        text=payload.message_text,
        settings=settings,
    )


async def deliver_whatsapp_live(
    payload: WorkerPayload,
    settings: WorkerSettings,
) -> WorkerResult:
    """Backward-compatible alias for Cloud API delivery."""
    return await deliver_whatsapp_cloud_live(payload, settings)
