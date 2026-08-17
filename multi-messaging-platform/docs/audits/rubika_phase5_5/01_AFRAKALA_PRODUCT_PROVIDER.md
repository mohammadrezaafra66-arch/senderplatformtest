# AfraKala Product Provider

## LIVE_AFRAKALA_BINDING

**CONFIG_PENDING**

No in-repo evidence of the real AfraKala Assistant Server URL, JSON schema, or auth contract. This phase does **not** invent REST paths such as `/products` or `/api/v1/products`.

## Interface

`ProductFeedProvider` protocol:

```
fetch_advertising_products(*, clock=None) -> ProductFeedResult
```

Implementations:

- `HttpJsonProductFeedProvider` — live adapter. GET the **complete** configured URL only.
- `FakeProductFeedProvider` — tests. No network.

## Configuration (environment only)

| Name | Role |
|---|---|
| `AFRAKALA_PRODUCT_API_BASE_URL` | Complete feed URL. Empty → CONFIG_PENDING |
| `AFRAKALA_PRODUCT_API_TOKEN` | Bearer token. Never logged, never sent to frontend |
| `AFRAKALA_PRODUCT_API_TIMEOUT_SECONDS` | Default 8. Connect timeout capped at 3s |
| `AFRAKALA_PRODUCT_MAX_STALENESS_SECONDS` | Default 300. Fail closed when exceeded |
| `AFRAKALA_PRODUCT_PRICE_CURRENCY` | Adapter default when row omits currency (default `IRR`) |
| `AFRAKALA_PRODUCT_PRICE_DISPLAY_UNIT` | Display label only (default `ریال`). **No conversion** |

`.env.example` lists names/placeholders only. No real token.

## HTTP behavior

- Timeout required; at most **one** retry on connect/timeout for GET.
- Response size cap 2_000_000 bytes.
- TLS/URL validated (`http`/`https` + netloc). Operator-configured URL only — never user-controlled.
- List payload accepted as a JSON array **or** an object with `data` / `items` / `products` / `results`. This is a generic JSON envelope, not a fabricated AfraKala path.
- Secrets redacted in logs (`event=product_feed_fetch_*`).

## Freshness

- Store `fetched_at` always.
- Store `source_updated_at` when the provider row supplies it.
- If no source timestamp exists, freshness is measured from **fetch time only**.
- Stale feed → `PRODUCT_FEED_STALE` (fail closed for `include_products` campaigns).
