"""Canonical AfraKala advertising-product DTOs.

Independent of any external provider schema. Product name/price/id are
immutable facts after freeze.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class AdvertisingProduct:
    external_id: str
    name: str
    price: Decimal
    currency: str
    advertising: bool
    product_code: str | None = None
    source_updated_at: datetime | None = None
    source: str = "afrakala"
    fetched_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "product_code": self.product_code,
            "name": self.name,
            "price": str(self.price),
            "currency": self.currency,
            "advertising": self.advertising,
            "source_updated_at": self.source_updated_at.isoformat()
            if self.source_updated_at
            else None,
            "source": self.source,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


@dataclass(frozen=True, slots=True)
class FrozenProductFact:
    """Persisted per-message product fact. Never rewritten after freeze."""

    external_id: str
    name: str
    price: str  # exact canonical numeric string
    currency: str
    display_price: str
    product_code: str | None = None
    source_updated_at: str | None = None
    fetched_at: str | None = None
    source: str = "afrakala"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_product(
        cls,
        product: AdvertisingProduct,
        *,
        display_price: str,
        fetched_at: datetime | None,
    ) -> "FrozenProductFact":
        return cls(
            external_id=product.external_id,
            name=product.name,
            price=format(product.price, "f"),
            currency=product.currency,
            display_price=display_price,
            product_code=product.product_code,
            source_updated_at=product.source_updated_at.isoformat()
            if product.source_updated_at
            else None,
            fetched_at=(fetched_at or product.fetched_at).isoformat()
            if (fetched_at or product.fetched_at)
            else None,
            source=product.source,
        )


@dataclass(frozen=True, slots=True)
class FrozenProductSnapshot:
    """Message-level immutable selection."""

    products: tuple[FrozenProductFact, ...]
    heading: str
    fetched_at: str | None
    source: str
    provider: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "products": [p.to_dict() for p in self.products],
            "heading": self.heading,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FrozenProductSnapshot":
        products = tuple(
            FrozenProductFact(**item) for item in (data.get("products") or [])
        )
        return cls(
            products=products,
            heading=str(data.get("heading") or ""),
            fetched_at=data.get("fetched_at"),
            source=str(data.get("source") or "afrakala"),
            provider=str(data.get("provider") or "unknown"),
        )


@dataclass(frozen=True, slots=True)
class MessageComposition:
    """GPT-safe composition: prose may later vary; product block may not."""

    prose_text: str
    immutable_product_block: str
    final_text: str
    heading: str
    snapshot: FrozenProductSnapshot | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ProductFeedResult:
    products: tuple[AdvertisingProduct, ...]
    fetched_at: datetime
    provider: str
    discarded_invalid: int = 0
    source_updated_at: datetime | None = None
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
