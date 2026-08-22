"""Product-feed fetch, freshness, and campaign composition entrypoints."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from core_engine.config import get_settings
from core_engine.services.product_feed.composition import compose_campaign_message
from core_engine.services.product_feed.dto import (
    FrozenProductSnapshot,
    MessageComposition,
    ProductFeedResult,
)
from core_engine.services.product_feed.errors import (
    INSUFFICIENT_ADVERTISING_PRODUCTS,
    PRODUCT_FEED_EMPTY,
    PRODUCT_FEED_STALE,
    ProductFeedError,
)
from core_engine.services.product_feed.afrakala_public_bot_provider import (
    AfraKalaPublicBotProductFeedProvider,
)
from core_engine.services.product_feed.provider import ProductFeedProvider
from core_engine.services.product_feed.selection import (
    MAX_PRODUCTS,
    MIN_PRODUCTS,
    select_advertising_products,
)

logger = logging.getLogger("core_engine.services.product_feed")

_provider_override: ProductFeedProvider | None = None


def set_product_feed_provider(provider: ProductFeedProvider | None) -> None:
    global _provider_override
    _provider_override = provider


def get_product_feed_provider() -> ProductFeedProvider:
    if _provider_override is not None:
        return _provider_override
    return AfraKalaPublicBotProductFeedProvider.from_settings()


def _now(clock: datetime | None) -> datetime:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _max_staleness_seconds() -> int:
    settings = get_settings()
    return int(getattr(settings, "AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS", 300) or 300)


def _unit_label() -> str:
    settings = get_settings()
    return str(getattr(settings, "AFRAKALA_PRODUCT_PRICE_DISPLAY_UNIT", "ریال") or "ریال")


def _assert_fresh(result: ProductFeedResult, *, clock: datetime | None) -> None:
    now = _now(clock)
    # Prefer provider source timestamp; otherwise freshness is fetch time only.
    reference = result.source_updated_at or result.fetched_at
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    age = (now - reference.astimezone(timezone.utc)).total_seconds()
    limit = _max_staleness_seconds()
    if age > limit:
        logger.warning(
            "event=product_feed_stale provider=%s age_seconds=%s limit=%s",
            result.provider,
            int(age),
            limit,
        )
        raise ProductFeedError(
            PRODUCT_FEED_STALE,
            details={"age_seconds": int(age), "limit_seconds": limit},
        )


def fetch_current_advertising_products(
    *,
    provider: ProductFeedProvider | None = None,
    clock: datetime | None = None,
    require_minimum: bool = True,
) -> ProductFeedResult:
    active = provider or get_product_feed_provider()
    result = active.fetch_advertising_products(clock=clock)
    _assert_fresh(result, clock=clock)
    if require_minimum and len(result.products) < MIN_PRODUCTS:
        code = PRODUCT_FEED_EMPTY if not result.products else INSUFFICIENT_ADVERTISING_PRODUCTS
        raise ProductFeedError(
            code,
            details={"eligible": len(result.products), "required": MIN_PRODUCTS},
        )
    return result


def select_and_compose(
    prose_text: str,
    feed: ProductFeedResult,
    *,
    rng: random.Random | None = None,
    heading: str | None = None,
) -> MessageComposition:
    selected = select_advertising_products(feed.products, rng)
    logger.info(
        "event=product_selection_created product_count=%s provider=%s",
        len(selected),
        feed.provider,
    )
    composition = compose_campaign_message(
        prose_text,
        selected,
        heading=heading,
        rng=rng,
        fetched_at=feed.fetched_at,
        provider=feed.provider,
        unit_label=_unit_label(),
    )
    logger.info(
        "event=product_snapshot_frozen product_count=%s heading_set=%s",
        len(selected),
        bool(composition.heading),
    )
    return composition


def compose_from_frozen_snapshot(
    prose_text: str,
    snapshot: FrozenProductSnapshot,
) -> MessageComposition:
    return compose_campaign_message(prose_text, snapshot=snapshot)


def product_feed_status(
    *,
    provider: ProductFeedProvider | None = None,
    clock: datetime | None = None,
) -> dict[str, Any]:
    """Operator preflight. Never returns secrets."""
    try:
        result = fetch_current_advertising_products(
            provider=provider, clock=clock, require_minimum=False
        )
        return {
            "ok": len(result.products) >= MIN_PRODUCTS,
            "code": "OK" if len(result.products) >= MIN_PRODUCTS else INSUFFICIENT_ADVERTISING_PRODUCTS,
            "eligible_count": len(result.products),
            "discarded_invalid": result.discarded_invalid,
            "fetched_at": result.fetched_at.isoformat(),
            "source_updated_at": result.source_updated_at.isoformat()
            if result.source_updated_at
            else None,
            "provider": result.provider,
            "min_required": MIN_PRODUCTS,
            "max_per_message": MAX_PRODUCTS,
            "live_binding": (
                "UNVERIFIED"
                if (getattr(get_settings(), "AFRAKALA_PRODUCT_API_BASE_URL", "") or "").strip()
                else "CONFIG_PENDING"
            ),
            "freshness_basis": "source_updated_at"
            if result.source_updated_at is not None
            else "fetched_at",
            "message": (
                f"{len(result.products)} محصول تبلیغاتی آماده"
                if len(result.products) >= MIN_PRODUCTS
                else "محصولات تبلیغاتی کافی نیست."
            ),
        }
    except ProductFeedError as exc:
        settings = get_settings()
        live = "CONFIG_PENDING"
        if (getattr(settings, "AFRAKALA_PRODUCT_API_BASE_URL", "") or "").strip():
            live = "UNVERIFIED"
        logger.warning(
            "event=product_feed_fetch_failed code=%s",
            exc.code,
        )
        return {
            "ok": False,
            "code": exc.code,
            "eligible_count": 0,
            "discarded_invalid": 0,
            "fetched_at": None,
            "source_updated_at": None,
            "provider": (provider or get_product_feed_provider()).name
            if hasattr(provider or get_product_feed_provider(), "name")
            else "unknown",
            "min_required": MIN_PRODUCTS,
            "max_per_message": MAX_PRODUCTS,
            "live_binding": live if exc.code != "CONFIG_PENDING" else "CONFIG_PENDING",
            "freshness_basis": "fetched_at",
            "message": exc.message,
        }


def snapshot_from_queue_payload(payload: dict[str, Any] | None) -> FrozenProductSnapshot | None:
    if not payload:
        return None
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else payload
    raw = None
    if isinstance(meta, dict):
        raw = meta.get("frozen_product_snapshot")
    if not isinstance(raw, dict):
        return None
    return FrozenProductSnapshot.from_dict(raw)
