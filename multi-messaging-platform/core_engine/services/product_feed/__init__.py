"""AfraKala advertising product feed (Phase 5.5)."""

from core_engine.services.product_feed.composition import (
    CONTROLLED_HEADINGS,
    compose_campaign_message,
    compose_with_locked_products,
)
from core_engine.services.product_feed.dto import (
    AdvertisingProduct,
    FrozenProductSnapshot,
    MessageComposition,
)
from core_engine.services.product_feed.errors import ProductFeedError
from core_engine.services.product_feed.service import (
    compose_from_frozen_snapshot,
    fetch_current_advertising_products,
    get_product_feed_provider,
    product_feed_status,
    select_and_compose,
    set_product_feed_provider,
    snapshot_from_queue_payload,
)

__all__ = [
    "AdvertisingProduct",
    "CONTROLLED_HEADINGS",
    "FrozenProductSnapshot",
    "MessageComposition",
    "ProductFeedError",
    "compose_campaign_message",
    "compose_from_frozen_snapshot",
    "compose_with_locked_products",
    "fetch_current_advertising_products",
    "get_product_feed_provider",
    "product_feed_status",
    "select_and_compose",
    "set_product_feed_provider",
    "snapshot_from_queue_payload",
]
