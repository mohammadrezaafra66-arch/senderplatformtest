# Rubika Phase 1 Summary — Account & Session Hardening

**Branch:** `feature/rubika-module`  
**START_HEAD:** `ec19f4a8fec6848499d4ef42b315c7214675afe6`  
**Migrations:** NONE

## Already VERIFIED (pre-implementation)

1. Bot API token encrypt/store/load path (`register_api_token_session`, connectors).
2. User-account OTP happy path + Redis TTL + encrypted envelope persist.
3. Latest-row ChannelSession load semantics (`id.desc()`).
4. Pool fail → RESTING / permanent → BANNED; restore RESTING → ACTIVE.
5. Delivery routing `bot_api` vs `user_account` in `workers/delivery.py`.
6. Session status / register / OTP API endpoints existed.

## PARTIAL (hardened this phase)

1. Delivery-mode validation only in workers (core_engine lacked reject).
2. `required_session_type` silent fallback on invalid Rubika mode.
3. Readiness result lacked structured `code` / Rubika `delivery_mode`.
4. Envelope had no version field.
5. OTP failure/duplicate/Redis-error tests missing; API lacked mode gates.
6. Session-invalid left accounts RESTING (not REQUIRES_LOGIN).
7. Frontend only showed binary ready/not-ready.

## Implemented

1. Central `core_engine/services/rubika_mode.py` — normalize, resolve, session-type mapping, registration/login asserts.
2. `core_engine.config` + `workers.config` both validate via shared normalizer.
3. Structured `SessionReadiness` with `code`, `session_type`, `delivery_mode` (+ legacy `error`).
4. Rubika readiness branches: USER_ACCOUNT_DISABLED, CONFIG_INVALID, ACCOUNT_DISABLED (RESTING), session missing/invalid.
5. Versioned user-account envelope (v1; legacy without version accepted).
6. OTP Redis error wrapping; API mode gates for bot token vs OTP login.
7. `mark_account_failed(requires_relogin=True)` → REQUIRES_LOGIN; connectors use it for auth/session invalid.
8. Minimal frontend readiness label mapping + delivery_mode/code display.
9. Comprehensive Phase 1 tests (`test_rubika_phase1_account_session.py`).

## Source files changed

- `core_engine/services/rubika_mode.py` (new)
- `core_engine/services/account_session_wiring.py`
- `core_engine/services/rubika_user_session.py`
- `core_engine/config.py`
- `core_engine/api/schemas.py`
- `core_engine/api/accounts.py`
- `workers/config.py`
- `workers/rubika_account_pool.py`
- `workers/connectors/rubika_user.py`
- `workers/rubika_ai_response_loop.py`
- `workers/rubika_group_listener.py`
- `workers/rubika_status_bot.py`
- `frontend/src/utils/session-readiness.ts` (new)
- `frontend/src/components/ApiTokenSessionPanel.tsx`
- `frontend/src/types/account.ts`
- `frontend/locales/fa/common.json`
- `tests/core_engine/test_rubika_phase1_account_session.py` (new)
- `docs/audits/rubika_phase1_account_session_baseline.md`
- `docs/audits/rubika_phase1/*`

## Tests added

`tests/core_engine/test_rubika_phase1_account_session.py` covering A–L:
mode contract, bot_api validation, envelope versioning, persistence/restart, duplicate latest-row, readiness matrix, OTP expired/wrong/duplicate/Redis failure, account transitions, config isolation.

## Backward compatibility

- Existing snake_case readiness `error` values preserved (`session_missing`, etc.).
- Envelope without `version` treated as v1.
- Modes remain exactly `bot_api` / `user_account` (no alias breakage).
- API response gains optional `code` / `delivery_mode` / Rubika deploy fields.

## Known remaining P0/P1 gaps

- Warm-up / rate-limit redesign / circuit breaker (later phases).
- Frontend still shows OTP panel by manual toggle on accounts page (not solely readiness-driven).
- Duplicate ChannelSession rows accumulate (latest wins; no unique constraint / prune job).

## Next phase recommendation

**PHASE 2 — RUBIKA PREFLIGHT / READINESS ENFORCEMENT**  
Enforce structured readiness before enqueue/send; reject wrong-mode operations at worker boundary with the same codes.
