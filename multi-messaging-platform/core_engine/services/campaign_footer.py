"""Canonical immutable footer for campaign messages."""

from __future__ import annotations

from core_engine.config import get_settings


def build_campaign_footer() -> str:
    """Return operator-controlled CTA/footer text."""
    settings = get_settings()

    if not settings.CAMPAIGN_FOOTER_ENABLED:
        return ""

    sections: list[str] = []

    channels = (
        ("\U0001F53B\u06a9\u0627\u0646\u0627\u0644 \u0648\u0627\u062a\u0633\u0627\u067e", settings.CAMPAIGN_WHATSAPP_CHANNEL_URL),
        ("\U0001F53B\u06a9\u0627\u0646\u0627\u0644 \u062a\u0644\u06af\u0631\u0627\u0645", settings.CAMPAIGN_TELEGRAM_CHANNEL_URL),
        ("\U0001F53B\u06a9\u0627\u0646\u0627\u0644 \u0631\u0648\u0628\u06cc\u06a9\u0627", settings.CAMPAIGN_RUBIKA_CHANNEL_URL),
        ("\U0001F53B\u06a9\u0627\u0646\u0627\u0644 \u0628\u0644\u0647", settings.CAMPAIGN_BALE_CHANNEL_URL),
        ("\U0001F53B\u06a9\u0627\u0646\u0627\u0644 \u0633\u0631\u0648\u0634", settings.CAMPAIGN_SOROUSH_CHANNEL_URL),
    )

    for label, raw_url in channels:
        url = (raw_url or "").strip()
        if url:
            sections.append(f"{label}\n{url}")

    phone = (settings.CAMPAIGN_CONTACT_PHONE or "").strip()
    if phone:
        sections.append(
            f"\u260E\uFE0F \u062a\u0644\u0641\u0646 \u062b\u0627\u0628\u062a\n{phone}"
        )

    return "\n\n".join(sections)


def append_campaign_footer(text: str) -> str:
    """Append canonical footer exactly once after all generated content."""
    base = (text or "").rstrip()
    footer = build_campaign_footer()

    if not footer:
        return base

    if base.endswith(footer):
        return base

    if not base:
        return footer

    return f"{base}\n\n{footer}"
