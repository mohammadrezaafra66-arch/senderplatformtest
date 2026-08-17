# Canonical Product DTO and Currency

## AdvertisingProduct (internal, frozen)

| Field | Rule |
|---|---|
| `external_id` | Required (sku/id/code). Traceability |
| `product_code` | Optional |
| `name` | Non-empty |
| `price` | `Decimal`, non-negative. Floats rejected (no binary guesswork) |
| `currency` | Explicit on row **or** adapter config. Never guessed |
| `advertising` | Must be true to be eligible |
| `source_updated_at` | Optional |
| `source` | Default `afrakala` |
| `fetched_at` | Set at fetch |

Frontend-provided name/price is never authoritative.

## FrozenProductFact / FrozenProductSnapshot

Persisted per **RenderedMessage** (not per campaign):

- exact `name`
- exact numeric `price` string (`format(decimal, "f")`)
- exact `currency`
- `display_price` from centralized formatter
- `external_id` / `product_code`
- `source_updated_at` / `fetched_at`
- `provider`

## Currency / unit safety

The provider contract makes unit explicit:

1. Row `currency` / `price_currency` / `unit` if present.
2. Else `AFRAKALA_PRODUCT_PRICE_CURRENCY` (default `IRR`).
3. Display label `AFRAKALA_PRODUCT_PRICE_DISPLAY_UNIT` (default `ریال`) is **formatting only**.

**Never** multiply or divide by 10 to convert Rial ↔ Toman.

Tests assert `28500000` renders as `۲۸,۵۰۰,۰۰۰ ریال` and that the accidental `/10` form `۲,۸۵۰,۰۰۰` is absent.

Rounding: integers keep exact integer display with grouping separators; non-integers keep the exact fractional digits of the canonical Decimal. No extra rounding.
