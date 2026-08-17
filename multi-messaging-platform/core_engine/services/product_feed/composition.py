"""Canonical product-block formatter. Facts never rewritten."""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from core_engine.services.product_feed.dto import (
    AdvertisingProduct,
    FrozenProductFact,
    FrozenProductSnapshot,
    MessageComposition,
)

CONTROLLED_HEADINGS = (
    "محصولات ویژه امروز:",
    "محصولاتی که با قیمت استثنایی امروز به فروش می‌رسند:",
)

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def format_price_display(price: Decimal | int | str, unit_label: str) -> str:
    """Format exact canonical price. No rial/toman conversion."""
    number = Decimal(str(price))
    if number == number.to_integral_value():
        formatted = f"{int(number):,}"
    else:
        text = format(number, "f")
        if "." in text:
            whole, frac = text.split(".", 1)
            formatted = f"{int(whole):,}.{frac}"
        else:
            formatted = f"{int(text):,}"
    return f"{formatted.translate(_PERSIAN_DIGITS)} {unit_label}".strip()


def freeze_products(
    selected: Sequence[AdvertisingProduct],
    *,
    heading: str,
    fetched_at: datetime | None,
    provider: str,
    unit_label: str,
) -> FrozenProductSnapshot:
    facts = tuple(
        FrozenProductFact.from_product(
            product,
            display_price=format_price_display(product.price, unit_label),
            fetched_at=fetched_at or product.fetched_at,
        )
        for product in selected
    )
    fetched_iso = None
    if fetched_at is not None:
        fetched_iso = fetched_at.isoformat()
    elif selected and selected[0].fetched_at is not None:
        fetched_iso = selected[0].fetched_at.isoformat()
    return FrozenProductSnapshot(
        products=facts,
        heading=heading,
        fetched_at=fetched_iso,
        source="afrakala",
        provider=provider,
    )


def render_product_block(snapshot: FrozenProductSnapshot) -> str:
    lines = [snapshot.heading, ""]
    for fact in snapshot.products:
        lines.append(f"{fact.name} — {fact.display_price}")
    return "\n".join(lines).rstrip()


def compose_campaign_message(
    prose_text: str,
    selected: Sequence[AdvertisingProduct] | None = None,
    *,
    snapshot: FrozenProductSnapshot | None = None,
    heading: str | None = None,
    rng: random.Random | None = None,
    fetched_at: datetime | None = None,
    provider: str = "unknown",
    unit_label: str = "ریال",
) -> MessageComposition:
    """Shared composition for preview and real render.

    Future GPT may rewrite ``prose_text`` only. ``immutable_product_block``
    is locked to the frozen snapshot.
    """
    prose = (prose_text or "").rstrip()
    if snapshot is None:
        if not selected:
            return MessageComposition(
                prose_text=prose,
                immutable_product_block="",
                final_text=prose,
                heading="",
                snapshot=None,
            )
        chosen_heading = heading
        if chosen_heading not in CONTROLLED_HEADINGS:
            chooser = rng or random.Random()
            chosen_heading = chooser.choice(CONTROLLED_HEADINGS)
        snapshot = freeze_products(
            selected,
            heading=chosen_heading,
            fetched_at=fetched_at,
            provider=provider,
            unit_label=unit_label,
        )
    block = render_product_block(snapshot)
    if prose:
        final_text = f"{prose}\n\n{block}"
    else:
        final_text = block
    return MessageComposition(
        prose_text=prose,
        immutable_product_block=block,
        final_text=final_text,
        heading=snapshot.heading,
        snapshot=snapshot,
    )


def compose_with_locked_products(prose_text: str, snapshot: FrozenProductSnapshot) -> MessageComposition:
    """Phase 5.6 hook: GPT may supply new prose; products stay locked."""
    return compose_campaign_message(prose_text, snapshot=snapshot)
