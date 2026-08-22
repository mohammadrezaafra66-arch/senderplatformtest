#!/usr/bin/env python3
"""MANUAL_LIVE_TEST — read-only AfraKala product feed + price-age diagnostics.

Requires:
  MANUAL_LIVE_TEST=1
  PILOT_CONFIRM=SEND
  AFRAKALA_PRODUCT_API_BASE_URL
  AFRAKALA_PRODUCT_API_TOKEN

Never prints tokens. Never mutates. Never run from CI.
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_engine.config import get_settings
from core_engine.services.pilot_profiles import require_manual_live_confirmation
from core_engine.services.product_feed.afrakala_public_bot_provider import (
    AfraKalaPublicBotProductFeedProvider,
)
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.service import (
    fetch_current_advertising_products,
    filter_fresh_products,
)

OBSERVATION_LIMITS = (300, 1800, 3600, 5400, 7200, 10800, 14400)


def main() -> int:
    try:
        require_manual_live_confirmation()
    except RuntimeError as exc:
        print(f"REFUSED {exc}")
        return 2

    settings = get_settings()
    base_url = (getattr(settings, "AFRAKALA_PRODUCT_API_BASE_URL", "") or "").strip()
    if not base_url:
        print("CONFIG_PENDING AFRAKALA_PRODUCT_API_BASE_URL empty")
        return 3

    started = time.monotonic()
    try:
        feed = fetch_current_advertising_products(require_minimum=False)
    except ProductFeedError as exc:
        print("status", exc.code)
        print("ok", False)
        print("details", exc.details)
        return 1

    latency_ms = int((time.monotonic() - started) * 1000)
    discard_reasons = Counter(feed.diagnostics)

    print("ok", True)
    print("fetched_at", feed.fetched_at.isoformat() if feed.fetched_at else None)
    print("campaign_eligible_count", len(feed.products))
    print("invalid_discarded", feed.discarded_invalid)
    print("invalid_discard_reasons", dict(discard_reasons))
    print("latency_ms", latency_ms)

    raw = AfraKalaPublicBotProductFeedProvider.from_settings().fetch_advertising_products()
    _, stats = filter_fresh_products(raw)
    print("fetch_age_seconds", stats.fetch_age_seconds)
    print("oldest_price_age_seconds", stats.oldest_price_age_seconds)
    print("newest_price_age_seconds", stats.newest_price_age_seconds)

    now = feed.fetched_at
    for limit in OBSERVATION_LIMITS:
        over_limit = 0
        for product in raw.products:
            if product.source_updated_at is None:
                continue
            age = (now - product.source_updated_at).total_seconds()
            if age > limit:
                over_limit += 1
        print(f"observed_price_age_over_{limit}s", over_limit)

    if feed.products:
        sample = feed.products[0]
        print(
            "sample_product",
            {
                "external_id": sample.external_id,
                "name": sample.name,
                "price": str(sample.price),
                "currency": sample.currency,
                "advertising": sample.advertising,
                "product_code": sample.product_code,
                "source_updated_at": sample.source_updated_at.isoformat()
                if sample.source_updated_at
                else None,
                "source": sample.source,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
