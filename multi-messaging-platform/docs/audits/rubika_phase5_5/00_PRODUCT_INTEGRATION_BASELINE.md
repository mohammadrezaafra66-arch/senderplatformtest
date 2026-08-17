# Phase 5.5 — Product Integration Baseline

START_HEAD: `b41c6a525bb039731830fbaea66f367b1fe3445b`  
BRANCH: `feature/rubika-module`  
Working tree at audit start: dirty with this phase's implementation only (no unknown third-party edits).

## Classification legend

- ALREADY_VERIFIED — present and reused as-is
- PARTIAL — present but not sufficient for advertising live injection
- MISSING — required for Phase 5.5
- CONFLICTING — two sources of truth that must not be mixed

## Campaign checkbox / fields

| Capability | Classification | Notes |
|---|---|---|
| UI checkbox «شامل محصولات» | ALREADY_VERIFIED | `frontend/src/pages/campaigns/create.tsx` |
| UI checkbox «استفاده از GPT» | ALREADY_VERIFIED | Present; Phase 5.5 does not implement rewriting |
| `Campaign.include_products` | ALREADY_VERIFIED | Boolean column; create-from-import persists it |
| `Campaign.use_gpt` | ALREADY_VERIFIED | Persisted; unused by this phase beyond compatibility |
| Explanatory product-feed copy | MISSING → added | Hint + «بررسی محصولات» via backend status |

## Rendering / persistence

| Capability | Classification | Notes |
|---|---|---|
| `RenderedMessage.final_text` | ALREADY_VERIFIED | Worker sends this persisted text |
| `RenderedMessage.queue_payload` JSON | ALREADY_VERIFIED | Safe per-message freeze storage; **no new table** |
| `RenderedMessage.used_products` | ALREADY_VERIFIED | Reused |
| `product_snapshots` table / `product_snapshot_id` | PARTIAL / CONFLICTING | Campaign-level Redis/HTML pricing cache (`title`/`sku`/`cash_price`). **No advertising tag.** Not used as source of truth for Phase 5.5 injection |
| Template rendering (`{{first_name}}` …) | ALREADY_VERIFIED | Prose source for product composition |
| Phase 4 prepare + staging | ALREADY_VERIFIED | Integration point for freeze |
| Queue bridge / WorkerPayload | ALREADY_VERIFIED | Sends `final_text` / `message_text`; no re-render |
| Retry payload | ALREADY_VERIFIED | Increments `attempt` only |

## Product / AfraKala

| Capability | Classification | Notes |
|---|---|---|
| Real AfraKala Assistant API URL/schema | MISSING | No contract in repo. **Do not invent paths.** |
| `PRICING_API_URL` amin-hozoor board | PARTIAL / CONFLICTING | Existing scraper/cache. Not advertising-tagged. Not used for campaign product blocks |
| Canonical `AdvertisingProduct` DTO | MISSING → added | Independent of external schema |
| `ProductFeedProvider` abstraction | MISSING → added | `fetch_advertising_products` |
| Live HTTP adapter | MISSING → added | GET configured complete URL only; empty URL = CONFIG_PENDING |
| 3–5 selection + freeze | MISSING → added | Seeded RNG per recipient |
| Immutable product block at message end | MISSING → added | Controlled headings |
| `MessageComposition` prose vs product block | MISSING → added | GPT-safe for Phase 5.6 |
| `GET /campaigns/product-feed/status` | MISSING → added | Operator preflight; no secrets |

## Decision

- Backend provider is source of truth. Frontend never supplies name/price.
- Freeze in `RenderedMessage.queue_payload.metadata.frozen_product_snapshot`.
- Live binding remains **CONFIG_PENDING** until a real assistant contract is supplied.
- Existing campaign-level `product_snapshots` path is left in place for legacy render/GPT helpers; campaign **prepare** with `include_products=true` uses the advertising feed only.
