"""Canonical GPT variation DTOs. Independent of OpenAI response shapes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class MessageVariation:
    text: str
    label: str = ""
    variation_id: str = ""


@dataclass(frozen=True, slots=True)
class MessageVariationResult:
    provider: str
    model: str
    generated_at: datetime
    variations: tuple[MessageVariation, ...]
    request_id: str | None = None
    usage: dict[str, Any] | None = None
    latency_ms: int | None = None


@dataclass(frozen=True, slots=True)
class FrozenVariation:
    variation_id: str
    text: str
    label: str
    provider: str
    model: str
    generated_at: str
    generation_batch_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrozenVariation":
        return cls(
            variation_id=str(data.get("variation_id") or ""),
            text=str(data.get("text") or ""),
            label=str(data.get("label") or ""),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            generated_at=str(data.get("generated_at") or ""),
            generation_batch_id=str(data.get("generation_batch_id") or ""),
        )


@dataclass(frozen=True, slots=True)
class FrozenVariationPool:
    generation_batch_id: str
    provider: str
    model: str
    generated_at: str
    template_fingerprint: str
    variations: tuple[FrozenVariation, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_batch_id": self.generation_batch_id,
            "provider": self.provider,
            "model": self.model,
            "generated_at": self.generated_at,
            "template_fingerprint": self.template_fingerprint,
            "variations": [item.to_dict() for item in self.variations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrozenVariationPool":
        variations = tuple(
            FrozenVariation.from_dict(item)
            for item in (data.get("variations") or [])
            if isinstance(item, dict)
        )
        return cls(
            generation_batch_id=str(data.get("generation_batch_id") or ""),
            provider=str(data.get("provider") or ""),
            model=str(data.get("model") or ""),
            generated_at=str(data.get("generated_at") or ""),
            template_fingerprint=str(data.get("template_fingerprint") or ""),
            variations=variations,
        )
