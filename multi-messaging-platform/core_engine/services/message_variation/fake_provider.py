"""In-process fake variation provider. Never hits the network."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Callable

from core_engine.services.message_variation.dto import MessageVariation, MessageVariationResult
from core_engine.services.message_variation.errors import GptVariationError


class FakeMessageVariationProvider:
    name = "fake"

    def __init__(
        self,
        variations: list[str] | None = None,
        *,
        fail_code: str | None = None,
        fail_until: int = 0,
        builder: Callable[[str, int], list[str]] | None = None,
    ) -> None:
        self.fixed_variations = list(variations or [])
        self.fail_code = fail_code
        self.fail_until = fail_until
        self.builder = builder
        self.call_count = 0
        self.captured: list[dict[str, Any]] = []

    def generate_variations(
        self,
        *,
        template_text: str,
        protected_placeholders: Sequence[str],
        requested_count: int,
        language: str,
        instructions: str,
    ) -> MessageVariationResult:
        self.call_count += 1
        self.captured.append(
            {
                "template_text": template_text,
                "protected_placeholders": list(protected_placeholders),
                "requested_count": requested_count,
                "language": language,
                "instructions": instructions,
            }
        )
        if self.fail_code:
            always = self.fail_until <= 0
            if always or self.call_count <= self.fail_until:
                raise GptVariationError(self.fail_code)
        if self.builder is not None:
            texts = self.builder(template_text, requested_count)
        elif self.fixed_variations:
            texts = list(self.fixed_variations)
        else:
            texts = [
                f"{template_text} تنوع {index}"
                for index in range(1, requested_count + 1)
            ]
        variations = tuple(
            MessageVariation(text=text, label=f"v{index}", variation_id=f"fake-{index}")
            for index, text in enumerate(texts, start=1)
        )
        return MessageVariationResult(
            provider=self.name,
            model="fake-model",
            generated_at=datetime.now(timezone.utc),
            variations=variations,
            request_id="fake-request",
            usage=None,
            latency_ms=1,
        )
