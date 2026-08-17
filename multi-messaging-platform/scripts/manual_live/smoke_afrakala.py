#!/usr/bin/env python3
"""MANUAL_LIVE_TEST — one read-only AfraKala product-feed fetch. No mutation.

Requires:
  MANUAL_LIVE_TEST=1
  PILOT_CONFIRM=SEND
  AFRAKALA_PRODUCT_API_BASE_URL configured

Never dumps the catalog. Never run from CI.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_engine.config import get_settings
from core_engine.services.pilot_profiles import require_manual_live_confirmation
from core_engine.services.product_feed.http_provider import HttpJsonProductFeedProvider
from core_engine.services.product_feed.errors import ProductFeedError


def main() -> int:
    try:
        require_manual_live_confirmation()
    except RuntimeError as exc:
        print(f"REFUSED {exc}")
        return 2
    settings = get_settings()
    url = (getattr(settings, "AFRAKALA_PRODUCT_API_BASE_URL", "") or "").strip()
    if not url:
        print("CONFIG_PENDING AFRAKALA_PRODUCT_API_BASE_URL empty")
        return 3
    started = time.monotonic()
    try:
        feed = HttpJsonProductFeedProvider.from_settings().fetch_advertising_products()
    except ProductFeedError as exc:
        print("status", exc.code)
        print("ok", False)
        return 1
    latency_ms = int((time.monotonic() - started) * 1000)
    print("ok", True)
    print("eligible_count", len(feed.products))
    print("currency", getattr(settings, "AFRAKALA_PRODUCT_PRICE_CURRENCY", None))
    print("display_unit", getattr(settings, "AFRAKALA_PRODUCT_PRICE_DISPLAY_UNIT", None))
    print("latency_ms", latency_ms)
    print("fetched_at", feed.fetched_at.isoformat() if feed.fetched_at else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
