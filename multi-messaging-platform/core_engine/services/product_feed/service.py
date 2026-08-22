"""Product-feed fetch, freshness, and campaign composition entrypoints."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core_engine.config import get_settings
from core_engine.services.product_feed.composition import compose_campaign_message
from core_engine.services.product_feed.dto import (
    AdvertisingProduct,
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

# Live AfraKala Public Bot API: price.computed_at is last-calculation metadata,
# not an expiry signal. Campaign eligibility follows the live API response.
_AFRAKALA_PUBLIC_BOT_PROVIDER = "afrakala_public_bot_api"
_LIVE_API_FETCH_PROVIDERS = frozenset({_AFRAKALA_PUBLIC_BOT_PROVIDER})


@dataclass(frozen=True, slots=True)
class FreshnessFilterStats:
    eligible_before_freshness: int
    fresh_eligible: int
    stale_discarded: int
    missing_timestamp_discarded: int
    invalid_discarded: int
    limit_seconds: int
    fetch_age_seconds: int | None = None
    oldest_price_age_seconds: int | None = None
    newest_price_age_seconds: int | None = None

    @property
    def stale_discarded_total(self) -> int:
        return self.stale_discarded + self.missing_timestamp_discarded


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


def _normalize_product_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _uses_price_age_campaign_gate(provider: str) -> bool:
    """Legacy providers may reject products by source_updated_at age."""
    return provider not in _LIVE_API_FETCH_PROVIDERS


def _product_freshness_age_seconds(product: AdvertisingProduct, *, now: datetime) -> float | None:
    reference = _normalize_product_dt(product.source_updated_at)
    if reference is None:
        return None
    return (now - reference).total_seconds()


def _price_age_diagnostics(
    products: tuple[AdvertisingProduct, ...],
    *,
    now: datetime,
) -> tuple[int | None, int | None]:
    ages = [
        int(_product_freshness_age_seconds(product, now=now))
        for product in products
        if _product_freshness_age_seconds(product, now=now) is not None
    ]
    if not ages:
        return None, None
    return max(ages), min(ages)


def _fetch_age_seconds(result: ProductFeedResult, *, now: datetime) -> int:
    fetched = _normalize_product_dt(result.fetched_at)
    if fetched is None:
        return 0
    return int((now - fetched).total_seconds())


def _is_product_fresh_by_price_age(
    product: AdvertisingProduct,
    *,
    now: datetime,
    limit_seconds: int,
    provider: str,
) -> tuple[bool, str | None]:
    """Legacy path: reject when source_updated_at age exceeds limit."""
    reference = product.source_updated_at
    if reference is None:
        return True, None
    age = _product_freshness_age_seconds(product, now=now)
    if age is None:
        return True, None
    if age <= limit_seconds:
        return True, None
    return False, "stale_source_updated_at"


def _filter_afrakala_public_bot_products(
    result: ProductFeedResult,
    *,
    now: datetime,
    limit_seconds: int,
) -> tuple[ProductFeedResult, FreshnessFilterStats]:
    """Live API products are authoritative at fetch time; price age is diagnostic only."""
    products = tuple(result.products)
    oldest_age, newest_age = _price_age_diagnostics(products, now=now)
    stamps = [
        _normalize_product_dt(product.source_updated_at)
        for product in products
        if product.source_updated_at is not None
    ]
    source_updated = max(stamps) if stamps else None
    filtered = ProductFeedResult(
        products=products,
        fetched_at=result.fetched_at,
        provider=result.provider,
        discarded_invalid=result.discarded_invalid,
        source_updated_at=source_updated,
        diagnostics=result.diagnostics,
    )
    stats = FreshnessFilterStats(
        eligible_before_freshness=len(products),
        fresh_eligible=len(products),
        stale_discarded=0,
        missing_timestamp_discarded=0,
        invalid_discarded=result.discarded_invalid,
        limit_seconds=limit_seconds,
        fetch_age_seconds=_fetch_age_seconds(result, now=now),
        oldest_price_age_seconds=oldest_age,
        newest_price_age_seconds=newest_age,
    )
    return filtered, stats


def filter_fresh_products(
    result: ProductFeedResult,
    *,
    clock: datetime | None = None,
    limit_seconds: int | None = None,
) -> tuple[ProductFeedResult, FreshnessFilterStats]:
    """Apply provider-specific freshness rules.

    AfraKala Public Bot API: all advertising-eligible products from a successful
    fetch remain selectable; source_updated_at records last price/product update age
    for diagnostics only.

    Legacy providers: per-product source_updated_at age may reject products when
    older than AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS.
    """
    now = _now(clock)
    limit = _max_staleness_seconds() if limit_seconds is None else int(limit_seconds)

    if not _uses_price_age_campaign_gate(result.provider):
        return _filter_afrakala_public_bot_products(
            result,
            now=now,
            limit_seconds=limit,
        )

    fresh_products: list[AdvertisingProduct] = []
    stale_discarded = 0
    missing_discarded = 0
    stale_diagnostics: list[str] = []

    for product in result.products:
        is_fresh, reason = _is_product_fresh_by_price_age(
            product,
            now=now,
            limit_seconds=limit,
            provider=result.provider,
        )
        if is_fresh:
            fresh_products.append(product)
            continue
        if reason == "missing_source_updated_at":
            missing_discarded += 1
        else:
            stale_discarded += 1
        if reason:
            stale_diagnostics.append(reason)

    stamps = [
        _normalize_product_dt(product.source_updated_at)
        for product in fresh_products
        if product.source_updated_at is not None
    ]
    source_updated = max(stamps) if stamps else None

    filtered = ProductFeedResult(
        products=tuple(fresh_products),
        fetched_at=result.fetched_at,
        provider=result.provider,
        discarded_invalid=(
            result.discarded_invalid + stale_discarded + missing_discarded
        ),
        source_updated_at=source_updated,
        diagnostics=result.diagnostics + tuple(stale_diagnostics[:20]),
    )
    stats = FreshnessFilterStats(
        eligible_before_freshness=len(result.products),
        fresh_eligible=len(fresh_products),
        stale_discarded=stale_discarded,
        missing_timestamp_discarded=missing_discarded,
        invalid_discarded=result.discarded_invalid,
        limit_seconds=limit,
        fetch_age_seconds=_fetch_age_seconds(result, now=now),
        oldest_price_age_seconds=_price_age_diagnostics(tuple(fresh_products), now=now)[0],
        newest_price_age_seconds=_price_age_diagnostics(tuple(fresh_products), now=now)[1],
    )
    return filtered, stats


def _freshness_error_details(stats: FreshnessFilterStats) -> dict[str, int]:
    payload: dict[str, int] = {
        "eligible_before_freshness": stats.eligible_before_freshness,
        "fresh_eligible": stats.fresh_eligible,
        "stale_discarded": stats.stale_discarded_total,
        "invalid_discarded": stats.invalid_discarded,
        "required": MIN_PRODUCTS,
        "limit_seconds": stats.limit_seconds,
    }
    if stats.fetch_age_seconds is not None:
        payload["fetch_age_seconds"] = stats.fetch_age_seconds
    if stats.oldest_price_age_seconds is not None:
        payload["oldest_price_age_seconds"] = stats.oldest_price_age_seconds
    if stats.newest_price_age_seconds is not None:
        payload["newest_price_age_seconds"] = stats.newest_price_age_seconds
    return payload


def _enforce_fetch_freshness(result: ProductFeedResult, *, clock: datetime | None) -> None:
    """Reject reused/cached live API responses when fetched_at is too old."""
    if result.provider not in _LIVE_API_FETCH_PROVIDERS:
        return
    now = _now(clock)
    fetch_age = _fetch_age_seconds(result, now=now)
    limit = _max_staleness_seconds()
    if fetch_age > limit:
        logger.warning(
            "event=product_feed_fetch_stale provider=%s fetch_age_seconds=%s limit=%s",
            result.provider,
            fetch_age,
            limit,
        )
        raise ProductFeedError(
            PRODUCT_FEED_STALE,
            details={
                "fetch_age_seconds": fetch_age,
                "limit_seconds": limit,
                "eligible_before_freshness": len(result.products),
                "fresh_eligible": len(result.products),
            },
        )


def _enforce_freshness_minimum(
    stats: FreshnessFilterStats,
    *,
    require_minimum: bool,
    provider: str,
) -> None:
    if _uses_price_age_campaign_gate(provider):
        if stats.eligible_before_freshness > 0 and stats.fresh_eligible == 0:
            logger.warning(
                "event=product_feed_stale provider=%s eligible=%s limit=%s",
                provider,
                stats.eligible_before_freshness,
                stats.limit_seconds,
            )
            raise ProductFeedError(
                PRODUCT_FEED_STALE,
                details=_freshness_error_details(stats),
            )
    if not require_minimum:
        return
    if stats.fresh_eligible < MIN_PRODUCTS:
        code = (
            PRODUCT_FEED_EMPTY
            if stats.eligible_before_freshness == 0
            else INSUFFICIENT_ADVERTISING_PRODUCTS
        )
        raise ProductFeedError(
            code,
            details={
                **_freshness_error_details(stats),
                "eligible": stats.fresh_eligible,
            },
        )


def fetch_current_advertising_products(
    *,
    provider: ProductFeedProvider | None = None,
    clock: datetime | None = None,
    require_minimum: bool = True,
) -> ProductFeedResult:
    active = provider or get_product_feed_provider()
    result = active.fetch_advertising_products(clock=clock)
    _enforce_fetch_freshness(result, clock=clock)
    filtered, stats = filter_fresh_products(result, clock=clock)
    _enforce_freshness_minimum(
        stats,
        require_minimum=require_minimum,
        provider=result.provider,
    )
    return filtered


def _freshness_basis(provider: str) -> str:
    if provider in _LIVE_API_FETCH_PROVIDERS:
        return "api_fetch_fetched_at"
    return "per_product_source_updated_at"


def _status_payload_from_result(
    result: ProductFeedResult,
    stats: FreshnessFilterStats,
) -> dict[str, Any]:
    eligible_count = stats.fresh_eligible
    payload: dict[str, Any] = {
        "ok": eligible_count >= MIN_PRODUCTS,
        "code": "OK" if eligible_count >= MIN_PRODUCTS else INSUFFICIENT_ADVERTISING_PRODUCTS,
        "eligible_count": eligible_count,
        "advertising_eligible_count": stats.eligible_before_freshness,
        "fresh_eligible_count": eligible_count,
        "stale_discarded": stats.stale_discarded_total,
        "invalid_discarded": stats.invalid_discarded,
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
        "freshness_basis": _freshness_basis(result.provider),
        "freshness_limit_seconds": stats.limit_seconds,
        "message": (
            f"{eligible_count} محصول تبلیغاتی آماده"
            if eligible_count >= MIN_PRODUCTS
            else "محصولات تبلیغاتی کافی نیست."
        ),
    }
    if stats.fetch_age_seconds is not None:
        payload["fetch_age_seconds"] = stats.fetch_age_seconds
    if stats.oldest_price_age_seconds is not None:
        payload["oldest_price_age_seconds"] = stats.oldest_price_age_seconds
    if stats.newest_price_age_seconds is not None:
        payload["newest_price_age_seconds"] = stats.newest_price_age_seconds
    return payload


def _status_payload_from_error(
    exc: ProductFeedError,
    *,
    provider_name: str,
    stats: FreshnessFilterStats | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    live = "CONFIG_PENDING"
    if (getattr(settings, "AFRAKALA_PRODUCT_API_BASE_URL", "") or "").strip():
        live = "UNVERIFIED"
    details = dict(exc.details or {})
    if stats is not None:
        details = {**_freshness_error_details(stats), **details}
    logger.warning(
        "event=product_feed_fetch_failed code=%s",
        exc.code,
    )
    return {
        "ok": False,
        "code": exc.code,
        "eligible_count": int(details.get("fresh_eligible", 0)),
        "advertising_eligible_count": int(
            details.get("eligible_before_freshness", details.get("eligible", 0))
        ),
        "fresh_eligible_count": int(details.get("fresh_eligible", 0)),
        "stale_discarded": int(details.get("stale_discarded", 0)),
        "invalid_discarded": int(details.get("invalid_discarded", 0)),
        "discarded_invalid": int(details.get("invalid_discarded", 0)),
        "fetched_at": None,
        "source_updated_at": None,
        "provider": provider_name,
        "min_required": MIN_PRODUCTS,
        "max_per_message": MAX_PRODUCTS,
        "live_binding": live if exc.code != "CONFIG_PENDING" else "CONFIG_PENDING",
        "freshness_basis": _freshness_basis(provider_name),
        "freshness_limit_seconds": int(
            details.get("limit_seconds", _max_staleness_seconds())
        ),
        "message": exc.message,
        "details": details or None,
    }


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
    active = provider or get_product_feed_provider()
    provider_name = getattr(active, "name", "unknown")
    try:
        raw = active.fetch_advertising_products(clock=clock)
        _enforce_fetch_freshness(raw, clock=clock)
        filtered, stats = filter_fresh_products(raw, clock=clock)
        payload = _status_payload_from_result(filtered, stats)
        if stats.fresh_eligible < MIN_PRODUCTS and stats.eligible_before_freshness == 0:
            payload["ok"] = False
            payload["code"] = PRODUCT_FEED_EMPTY
            payload["details"] = _freshness_error_details(stats)
        return payload
    except ProductFeedError as exc:
        return _status_payload_from_error(
            exc,
            provider_name=provider_name,
            stats=None,
        )


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
