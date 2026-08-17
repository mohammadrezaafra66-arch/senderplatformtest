"""OpenAI Responses API variation provider. Server-side only."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from core_engine.config import get_settings
from core_engine.services.message_variation.dto import MessageVariation, MessageVariationResult
from core_engine.services.message_variation.errors import (
    GPT_INVALID_RESPONSE,
    GPT_NOT_CONFIGURED,
    GPT_RATE_LIMITED,
    GPT_TIMEOUT,
    GPT_UNAVAILABLE,
    GptVariationError,
)
from core_engine.services.openai_client import is_openai_api_key_configured

logger = logging.getLogger("core_engine.services.message_variation.openai")

VARIATION_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "variations": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "label": {"type": "string"},
                },
                "required": ["text", "label"],
            },
        }
    },
    "required": ["variations"],
}


class OpenAIMessageVariationProvider:
    name = "openai"

    def __init__(
        self,
        *,
        client: OpenAI | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client
        self.model = (model or settings.OPENAI_MODEL or "").strip()
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "OPENAI_TIMEOUT_SECONDS", 30) or 30
        )
        self.max_output_tokens = int(
            max_output_tokens
            if max_output_tokens is not None
            else getattr(settings, "OPENAI_MAX_OUTPUT_TOKENS", 1200) or 1200
        )

    def _client_or_raise(self) -> OpenAI:
        if self._client is not None:
            return self._client
        if not is_openai_api_key_configured() or not self.model:
            raise GptVariationError(GPT_NOT_CONFIGURED)
        settings = get_settings()
        return OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=self.timeout_seconds,
            max_retries=0,
        )

    def generate_variations(
        self,
        *,
        template_text: str,
        protected_placeholders: Sequence[str],
        requested_count: int,
        language: str,
        instructions: str,
    ) -> MessageVariationResult:
        client = self._client_or_raise()
        logger.info(
            "event=gpt_variation_generation_started provider=%s model=%s variation_count=%s",
            self.name,
            self.model,
            requested_count,
        )
        started = time.monotonic()
        try:
            response = client.responses.create(
                model=self.model,
                instructions=instructions,
                input=template_text,
                max_output_tokens=self.max_output_tokens,
                store=False,
                timeout=self.timeout_seconds,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "message_variations",
                        "strict": True,
                        "schema": VARIATION_JSON_SCHEMA,
                    }
                },
            )
        except APITimeoutError as exc:
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s",
                GPT_TIMEOUT,
                self.model,
            )
            raise GptVariationError(GPT_TIMEOUT) from exc
        except RateLimitError as exc:
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s",
                GPT_RATE_LIMITED,
                self.model,
            )
            raise GptVariationError(GPT_RATE_LIMITED) from exc
        except APIConnectionError as exc:
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s",
                GPT_UNAVAILABLE,
                self.model,
            )
            raise GptVariationError(GPT_UNAVAILABLE) from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", None)
            code = GPT_RATE_LIMITED if status == 429 else GPT_UNAVAILABLE
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s status=%s",
                code,
                self.model,
                status,
            )
            raise GptVariationError(code) from exc
        except Exception as exc:
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s",
                GPT_UNAVAILABLE,
                self.model,
            )
            raise GptVariationError(GPT_UNAVAILABLE) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        raw_text = _extract_output_text(response)
        try:
            payload = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                "event=gpt_variation_generation_failed code=%s model=%s",
                GPT_INVALID_RESPONSE,
                self.model,
            )
            raise GptVariationError(GPT_INVALID_RESPONSE) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("variations"), list):
            raise GptVariationError(GPT_INVALID_RESPONSE)

        items: list[MessageVariation] = []
        for index, row in enumerate(payload["variations"], start=1):
            if not isinstance(row, dict):
                raise GptVariationError(GPT_INVALID_RESPONSE)
            text = str(row.get("text") or "").strip()
            label = str(row.get("label") or f"v{index}")
            items.append(MessageVariation(text=text, label=label, variation_id=f"oai-{index}"))

        usage = _safe_usage(response)
        request_id = getattr(response, "id", None)
        logger.info(
            "event=gpt_variation_generation_succeeded provider=%s model=%s variation_count=%s latency_ms=%s",
            self.name,
            self.model,
            len(items),
            latency_ms,
        )
        return MessageVariationResult(
            provider=self.name,
            model=self.model,
            generated_at=datetime.now(timezone.utc),
            variations=tuple(items),
            request_id=str(request_id) if request_id else None,
            usage=usage,
            latency_ms=latency_ms,
        )


def _extract_output_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    chunks: list[str] = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            value = getattr(content, "text", None)
            if isinstance(value, str):
                chunks.append(value)
    return "".join(chunks)


def _safe_usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    if isinstance(usage, dict):
        return {
            key: usage.get(key)
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if key in usage
        }
    payload: dict[str, Any] = {}
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if value is not None:
            payload[key] = value
    return payload or None
