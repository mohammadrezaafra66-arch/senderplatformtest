#!/usr/bin/env python3
"""MANUAL_LIVE_TEST — read-only AfraKala freshness measurement at multiple thresholds.

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
from core_engine.services.product_feed.service import filter_fresh_products

OBSERVATION_LIMITS = (300, 600, 900, 1800)


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
        raw = AfraKalaPublicBotProductFeedProvider.from_settings().fetch_advertising_products()
    except ProductFeedError as exc:
        print("status", exc.code)
        print("ok", False)
        return 1

    latency_ms = int((time.monotonic() - started) * 1000)
    discard_reasons = Counter(raw.diagnostics)

    print("ok", True)
    print("fetched_at", raw.fetched_at.isoformat() if raw.fetched_at else None)
    print("provider_source_updated_at", raw.source_updated_at.isoformat() if raw.source_updated_at else None)
    print("advertising_eligible_count", len(raw.products))
    print("invalid_discarded", raw.discarded_invalid)
    print("invalid_discard_reasons", dict(discard_reasons))
    print("latency_ms", latency_ms)

    for limit in OBSERVATION_LIMITS:
        filtered, stats = filter_fresh_products(raw, limit_seconds=limit)
        print(
            f"fresh_at_{limit}s",
            stats.fresh_eligible,
            "stale_discarded",
            stats.stale_discarded_total,
        )

    if raw.products:
        sample = raw.products[0]
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

    configured_limit = int(
        getattr(settings, "AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS", 300) or 300
    )
    configured_filtered, configured_stats = filter_fresh_products(
        raw,
        limit_seconds=configured_limit,
    )
    print("configured_limit_seconds", configured_limit)
    print("configured_fresh_eligible", configured_stats.fresh_eligible)
    print("configured_stale_discarded", configured_stats.stale_discarded_total)
    print(
        "configured_source_updated_at",
        configured_filtered.source_updated_at.isoformat()
        if configured_filtered.source_updated_at
        else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
