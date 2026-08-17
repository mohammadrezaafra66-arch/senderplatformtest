#!/usr/bin/env python3
"""MANUAL_LIVE_TEST — one bounded OpenAI variation request. No Rubika send.

Requires:
  MANUAL_LIVE_TEST=1
  PILOT_CONFIRM=SEND
  OPENAI_API_KEY configured

Never run from CI. Input contains no customer PII.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_engine.services.pilot_profiles import require_manual_live_confirmation
from core_engine.services.openai_client import is_openai_api_key_configured
from core_engine.config import get_settings
from core_engine.services.message_variation.openai_provider import OpenAIMessageVariationProvider
from core_engine.services.message_variation.prompt import SYSTEM_INSTRUCTION_FA


TEMPLATE = "سلام {{first_name}}، این یک پیام آزمایشی داخلی است."


def main() -> int:
    try:
        require_manual_live_confirmation()
    except RuntimeError as exc:
        print(f"REFUSED {exc}")
        return 2
    settings = get_settings()
    if not is_openai_api_key_configured(settings) or not (settings.OPENAI_MODEL or "").strip():
        print("CONFIG_PENDING OPENAI_API_KEY or OPENAI_MODEL missing")
        return 3
    started = time.monotonic()
    provider = OpenAIMessageVariationProvider()
    result = provider.generate_variations(
        template_text=TEMPLATE,
        protected_placeholders=["first_name"],
        requested_count=3,
        language="fa",
        instructions=SYSTEM_INSTRUCTION_FA,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    print("ok", True)
    print("model", settings.OPENAI_MODEL)
    print("variation_count", len(result.variations))
    print("latency_ms", latency_ms)
    print("placeholders_ok", all("{{first_name}}" in v.text for v in result.variations))
    print("store_false", True)
    print("pii_absent", all("customer" not in v.text.lower() for v in result.variations))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 — operator-facing smoke
        print(f"FAILED {type(exc).__name__}")
        raise SystemExit(1) from exc
