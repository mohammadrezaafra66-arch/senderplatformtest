# Failure Behavior

Persian operator message (structured `HTTP 400` detail):

> اطلاعات محصولات و قیمت‌های لحظه‌ای در دسترس نیست؛ آماده‌سازی کمپین شامل محصولات متوقف شد.

## Codes

| Code | When |
|---|---|
| `CONFIG_PENDING` | Live URL empty / contract not bound |
| `PRODUCT_FEED_UNAVAILABLE` | Connect/HTTP failure |
| `PRODUCT_FEED_TIMEOUT` | Timeout after bounded GET retry |
| `PRODUCT_FEED_INVALID_RESPONSE` | Non-JSON, oversize, unknown shape |
| `PRODUCT_FEED_STALE` | Source/fetch older than max staleness |
| `PRODUCT_FEED_EMPTY` | Zero eligible advertising products |
| `PRODUCT_PRICE_INVALID` | Reserved; invalid prices are discarded per-row (`invalid_price`) |
| `INSUFFICIENT_ADVERTISING_PRODUCTS` | Fewer than 3 valid advertising products remain |

## Campaign prepare (`include_products=true`)

Fail **before** queueing. No staged / rendered / message rows. No silent text-only fallback.

`start_campaign` `_auto_prepare` **re-raises** these codes so start cannot swallow a feed outage.

## Partial feed

Malformed rows discarded; if ≥ 3 valid advertising products remain, continue.

## Observability (no secrets)

`product_feed_fetch_started` / `_succeeded` / `_failed` / `product_feed_stale` / `product_selection_created` / `product_snapshot_frozen`

Safe fields: campaign/message ids, product_count, provider, host (not token, not Authorization).
