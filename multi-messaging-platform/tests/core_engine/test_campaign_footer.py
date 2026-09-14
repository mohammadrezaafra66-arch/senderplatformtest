from __future__ import annotations

from types import SimpleNamespace

import core_engine.services.campaign_footer as campaign_footer
from core_engine.services.campaign_render import (
    RENDER_VERSION,
    compose_final_render,
    hash_final_text,
)


def _settings(*, enabled: bool = True):
    return SimpleNamespace(
        CAMPAIGN_FOOTER_ENABLED=enabled,
        CAMPAIGN_CONTACT_PHONE="02174393000",
        CAMPAIGN_WHATSAPP_CHANNEL_URL="https://whatsapp.example/channel",
        CAMPAIGN_TELEGRAM_CHANNEL_URL="https://t.me/example",
        CAMPAIGN_RUBIKA_CHANNEL_URL="https://rubika.ir/example",
        CAMPAIGN_BALE_CHANNEL_URL="https://ble.ir/example",
        CAMPAIGN_SOROUSH_CHANNEL_URL="https://splus.ir/example",
    )


def test_footer_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(
        campaign_footer,
        "get_settings",
        lambda: _settings(enabled=False),
    )
    assert campaign_footer.append_campaign_footer("message") == "message"


def test_footer_is_complete_unicode_safe_and_idempotent(monkeypatch):
    monkeypatch.setattr(
        campaign_footer,
        "get_settings",
        lambda: _settings(enabled=True),
    )

    footer = campaign_footer.build_campaign_footer()

    assert footer.startswith(
        "\U0001F53B\u06a9\u0627\u0646\u0627\u0644 "
        "\u0648\u0627\u062a\u0633\u0627\u067e"
    )
    assert (
        "\U0001F53B\u06a9\u0627\u0646\u0627\u0644 "
        "\u062a\u0644\u06af\u0631\u0627\u0645"
    ) in footer
    assert (
        "\U0001F53B\u06a9\u0627\u0646\u0627\u0644 "
        "\u0631\u0648\u0628\u06cc\u06a9\u0627"
    ) in footer

    assert "https://t.me/example" in footer
    assert "https://rubika.ir/example" in footer
    assert "https://ble.ir/example" in footer
    assert "https://splus.ir/example" in footer

    assert (
        "\u260E\uFE0F "
        "\u062a\u0644\u0641\u0646 "
        "\u062b\u0627\u0628\u062a\n02174393000"
    ) in footer

    once = campaign_footer.append_campaign_footer("message")
    twice = campaign_footer.append_campaign_footer(once)

    assert twice == once


def test_campaign_render_v2_freezes_footer_into_hash(monkeypatch):
    monkeypatch.setattr(
        campaign_footer,
        "get_settings",
        lambda: _settings(enabled=True),
    )

    result = compose_final_render(
        prose="message",
        render_batch_id="footer-test",
        template_source="base_template",
        use_gpt=False,
        include_products=False,
        substitute=False,
        sample=True,
    )

    assert RENDER_VERSION == "campaign-render-v2"
    assert result.render_version == "campaign-render-v2"
    assert result.final_text.startswith("message")
    assert "https://rubika.ir/example" in result.final_text
    assert result.final_text.endswith("02174393000")
    assert result.final_text_sha256 == hash_final_text(result.final_text)
