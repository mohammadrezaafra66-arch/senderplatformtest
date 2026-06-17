#!/usr/bin/env python3
"""Phase 4 Step 2 verification — schemas and utility helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_engine.schemas.phase4 import (  # noqa: E402
    CampaignCreateRequest,
    ContactImportItem,
    ContactImportRequest,
)
from core_engine.services.phase4_utils import (  # noqa: E402
    build_full_name,
    build_staged_queue_payload,
    is_consent_allowed,
    normalize_consent_status,
    normalize_phone,
    validate_campaign_channel,
)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def print_result(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def test_phone_normalization() -> None:
    section("Phone normalization")
    examples = [
        ("09120000000", "+989120000000"),
        ("9120000000", "+989120000000"),
        ("989120000000", "+989120000000"),
        ("00989120000000", "+989120000000"),
        ("۰۹۱۲۱۲۳۴۵۶۷", "+989121234567"),
        ("+98 912-000-0000", "+989120000000"),
    ]
    for raw, expected in examples:
        actual = normalize_phone(raw)
        status = "OK" if actual == expected else "FAIL"
        print_result(f"{status} {raw!r}", actual)
        if actual != expected:
            print_result("  expected", expected)


def test_consent_normalization() -> None:
    section("Consent normalization")
    examples = [
        ("allowed", "allowed"),
        ("blocked", "blocked"),
        ("unknown", "unknown"),
        ("opted_in", "allowed"),
        ("opted_out", "blocked"),
        ("OPTED_IN", "allowed"),
        (None, "unknown"),
        ("", "unknown"),
    ]
    for raw, expected in examples:
        actual = normalize_consent_status(raw)
        status = "OK" if actual == expected else "FAIL"
        print_result(f"{status} {raw!r}", actual)
        print_result(f"  is_consent_allowed({raw!r})", is_consent_allowed(raw or ""))


def test_campaign_channel_validation() -> None:
    section("Campaign channel validation")
    for channel in ("whatsapp", " TELEGRAM ", "Rubika", "bale"):
        print_result(channel, validate_campaign_channel(channel))

    for invalid in ("sms", "email", ""):
        try:
            validate_campaign_channel(invalid)
            print_result(f"FAIL {invalid!r}", "expected ValueError")
        except ValueError as exc:
            print_result(f"OK {invalid!r}", str(exc))


def test_build_full_name() -> None:
    section("build_full_name")
    examples = [
        ("Ali", "Rezaei", "Ali Rezaei"),
        (" Ali ", " Rezaei ", "Ali Rezaei"),
        ("Ali", None, "Ali"),
        (None, "Rezaei", "Rezaei"),
        (None, None, None),
        ("  ", "  ", None),
    ]
    for first, last, expected in examples:
        actual = build_full_name(first, last)
        status = "OK" if actual == expected else "FAIL"
        print_result(f"{status} ({first!r}, {last!r})", actual)


def test_schemas() -> None:
    section("Pydantic schema smoke checks")
    campaign = CampaignCreateRequest(
        name="Test Campaign",
        channel="whatsapp",
        daily_limit=100,
        max_contacts=50,
    )
    print_result("CampaignCreateRequest", campaign.model_dump())

    contact_request = ContactImportRequest(
        campaign_id=1,
        contacts=[
            ContactImportItem(phone="09120000000", consent_status="allowed"),
            ContactImportItem(phone="9121111111", consent_status="opted_out"),
        ],
    )
    print_result("ContactImportRequest contacts", len(contact_request.contacts))


def test_staged_queue_payload() -> None:
    section("build_staged_queue_payload (dry-run dict only)")
    payload = build_staged_queue_payload(
        campaign_id=1,
        contact_id=2,
        rendered_message_id=3,
        channel="telegram",
        phone="09120000000",
        channel_handle="@testuser",
        final_text="سلام",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    print("Phase 4 Step 2 verification")
    test_phone_normalization()
    test_consent_normalization()
    test_campaign_channel_validation()
    test_build_full_name()
    test_schemas()
    test_staged_queue_payload()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
