"""MessageVariationProvider protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from core_engine.services.message_variation.dto import MessageVariationResult


class MessageVariationProvider(Protocol):
    name: str

    def generate_variations(
        self,
        *,
        template_text: str,
        protected_placeholders: Sequence[str],
        requested_count: int,
        language: str,
        instructions: str,
    ) -> MessageVariationResult: ...
