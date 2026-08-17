# Runtime profiles

Defined in `core_engine/services/pilot_profiles.py`.

## PROFILE 1 — LOCAL_TEST

Meaning: automated tests / CI / local pytest.

- `REAL_QUEUE_PUSH_ENABLED=false`
- `REAL_MESSAGE_SENDING_ENABLED=false` (kill switch OFF)
- `CHANNEL_CONNECTORS_ENABLED=false`
- `DRY_RUN=false`
- `SHADOW_MODE=false`
- `OPS_LIVE_SEND_API_ENABLED=false`

Transport is impossible. Mocks only.

## PROFILE 2 — PILOT_SHADOW

Meaning: production-like data plane without Rubika transport.

Allowed: real DB, real Redis, session readiness inspection, campaign prepare/freeze, preflight/capacity, optional queue push, optional **read-only** AfraKala fetch if configured, optional OpenAI generation if configured (never sent to Rubika).

Blocked: real Rubika transport.

- `REAL_QUEUE_PUSH_ENABLED=true` (optional; still fail-closed at worker)
- **`REAL_MESSAGE_SENDING_ENABLED=false`** (authoritative kill switch)
- `CHANNEL_CONNECTORS_ENABLED=false`
- `DRY_RUN=false`
- `SHADOW_MODE=false`
- `OPS_LIVE_SEND_API_ENABLED=false`

Verified: worker router returns `real_send_disabled` and does not call connectors.

## PROFILE 3 — PILOT_LIVE

Meaning: owner-approved narrow real send. **Do not enable in this phase without OWNER GATE.**

Delta from PILOT_SHADOW:

- `REAL_MESSAGE_SENDING_ENABLED=true` (kill switch ON)
- `CHANNEL_CONNECTORS_ENABLED=true`
- `DRY_RUN=false`
- `SHADOW_MODE=false`
- `REAL_QUEUE_PUSH_ENABLED=true`
- Keep `OPS_LIVE_SEND_API_ENABLED=false` unless a separate API test send is approved

Obvious kill switch: **`REAL_MESSAGE_SENDING_ENABLED`**.

OFF → `deliver_platform_message` never reaches Rubika connectors, regardless of campaign state.

## LIVE AFRAKALA

Binding: **CONFIG_PENDING**

Repository/docs/env contain no confirmed assistant URL, method path, or schema beyond the generic HTTP JSON adapter:

- GET the fully configured `AFRAKALA_PRODUCT_API_BASE_URL` (no invented paths)
- Optional `Authorization: Bearer <token>`
- Advertising eligibility: explicit flags/tags only
- Price: integer/Decimal; default currency IRR; display unit ریال; **no conversion**
- Price unit empirically **UNKNOWN** until a read-only smoke against the real contract

Read-only smoke: **not run** (URL/token absent; would require `MANUAL_LIVE_TEST=1`).

Product-enabled live pilot: **not allowed**.

## LIVE OPENAI

Binding: **CONFIG_PENDING**

- Key present: no
- Model default: `gpt-4o-mini` (not a live proof)
- Provider uses Responses API `store=False`, JSON schema, placeholder guards
- Fail-closed prepare when `use_gpt=true` (Phase 5.6 tests)
- Synthetic smoke: **not run** (no key; script `scripts/manual_live/smoke_openai.py` gated)

GPT-enabled live pilot: **not allowed**.
Plain-text Rubika Pilot 1 is not blocked by these optional services.

