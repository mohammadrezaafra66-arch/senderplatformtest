# Advertising Filter and Selection

## Eligibility

A row is advertising **only** via explicit mapping:

- booleans: `advertising`, `is_advertising`, `is_promo`, `promotional`, `advertising_tagged`
- tags/labels containing: `advertising`, `promo`, `promotion`, `تبلیغاتی`, …

Never inferred from low price, category, stock, name, or discount.

Invalid rows are discarded individually with diagnostics. Remaining valid advertising products are then counted.

## Minimum inventory

- `< 3` eligible → `INSUFFICIENT_ADVERTISING_PRODUCTS`
- `0` eligible → `PRODUCT_FEED_EMPTY` (or CONFIG_PENDING / UNAVAILABLE if the fetch itself failed)
- Ordinary (non-advertising) products are **never** used as filler

## Selection

- Count: `randint(3, min(5, n))` via injected `random.Random`
- No duplicate `external_id` in one message
- Seed for prepare: `{campaign_id}:{contact_id}:{prepare_nonce}` so recipients can vary when inventory is large
- Same seed → same set (testable)
- After persistence, retry **does not** reselect

`include_products=false` → provider `call_count == 0`.
