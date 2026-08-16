# Rubika Phase 1 — Account & Session Baseline Audit

**Branch:** `feature/rubika-module`  
**START_HEAD:** `ec19f4a8fec6848499d4ef42b315c7214675afe6`  
**Audited before implementation:** yes  
**Scope:** Account state, session type, delivery mode, readiness, OTP, persistence, restart safety

Status legend: `VERIFIED` | `PARTIAL` | `MISSING` | `BROKEN` | `CONFIG_DEPENDENT`

---

## P1.1 — Explicit delivery mode

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Modes `bot_api` / `user_account` | VERIFIED | `workers/config.py` `normalize_rubika_delivery_mode`; `.env.example` |
| Worker rejects invalid mode | VERIFIED | `WorkerSettings` field_validator raises `ValueError` |
| core_engine rejects invalid mode | MISSING | `core_engine/config.py` `Settings.RUBIKA_DELIVERY_MODE` — no validator |
| No silent fallback in session wiring | BROKEN | `required_session_type()` returns `API_TOKEN` for any non-`user_account` string |
| Delivery routing | VERIFIED | `workers/delivery.py` branches on mode; `RUBIKA_USER_ACCOUNT_ENABLED` gate |
| Tests both modes | PARTIAL | `test_account_session_wiring.py` (override args); worker tests pin mode; invalid-mode test missing |

---

## P1.2 — Session type contract

**Status:** PARTIAL (centralized, but invalid mode not rejected)

| Mapping | Status | Evidence |
|---------|--------|----------|
| `bot_api` → `SessionType.API_TOKEN` | VERIFIED | `account_session_wiring.required_session_type` |
| `user_account` → `SessionType.RUBIKA_SESSION` | VERIFIED | same |
| Centralized (not duplicated) | VERIFIED | single function; connectors load by fixed type |
| Invalid mode | BROKEN | silent `API_TOKEN` fallback |
| Explicit override | VERIFIED | `rubika_delivery_mode=` kwarg |
| Default config path | PARTIAL | uses `get_settings().RUBIKA_DELIVERY_MODE` without normalize/reject |
| Tests | PARTIAL | bot_api + user_account covered; invalid/default-path gaps |

---

## P1.3 — Bot API session validation

**Status:** VERIFIED (with test gaps)

| Aspect | Status | Evidence |
|--------|--------|----------|
| Non-empty plain token | VERIFIED | `_validate_session_payload` |
| JSON with `bot_token`/`token`/`api_token` | VERIFIED | same |
| Empty rejected | VERIFIED | raises `ValueError` |
| Malformed JSON rejected | VERIFIED | `JSONDecodeError` → `ValueError` |
| Encrypted persist | VERIFIED | `register_api_token_session` → `store_channel_session` |
| Load | VERIFIED | `workers/session_access.load_account_session_plaintext`; connector `extract_rubika_bot_token` |
| Tests | PARTIAL | Bale/Telegram covered; Rubika-specific empty/malformed/roundtrip sparse (`test_rubika_connector.py`, phase8 e2e) |

---

## P1.4 — User account session envelope

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Fields `phone_number,auth,guid,user_agent,private_key` | VERIFIED | `rubika_user_session._ENVELOPE_FIELDS` |
| Versioned envelope | MISSING | no `version` field |
| Missing fields rejected | VERIFIED | `parse_session_envelope` |
| Malformed JSON | PARTIAL | raw `json.loads` may raise `JSONDecodeError` (not wrapped as `ValueError`) |
| Encrypted persist | VERIFIED | `verify_rubika_user_login` → `store_channel_session` |
| Roundtrip | VERIFIED | `test_build_and_parse_session_envelope_roundtrip` |
| No secret logging | VERIFIED | logs only `account_id` / `guid` / phone suffix |
| Tests | PARTIAL | missing/malformed/version gaps |

---

## P1.5 — Session readiness engine

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Centralized function | VERIFIED | `evaluate_account_session_readiness` |
| Structured result | PARTIAL | `SessionReadiness(ready, message, error)` — no `code`/`session_type`/`delivery_mode` |
| bot_api + user_account | PARTIAL | session type selected via config; no `USER_ACCOUNT_DISABLED` / `CONFIG_INVALID` codes |
| Banned / requires_login | VERIFIED | early returns |
| RESTING treated as not delivery-ready | MISSING | RESTING with valid session still `ready=True` |
| Rubika `delivery_mode` in API status | MISSING | `build_account_session_status` sets `delivery_mode=None` for Rubika |
| Tests | PARTIAL | `session_missing` on Bale; Rubika branch matrix incomplete |

---

## P1.6 — Account status transitions

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Enum states | VERIFIED | `ACTIVE/RESTING/BANNED/REQUIRES_LOGIN` — no new states needed |
| OTP success → ACTIVE | VERIFIED | `verify_rubika_user_login`; `test_rubika_user_login_flow_success` |
| Bot token register activates REQUIRES_LOGIN | VERIFIED | `register_api_token_session` |
| Temp failure → RESTING (not BANNED) | VERIFIED | `RubikaAccountPoolManager.mark_account_failed(permanent=False)` |
| Permanent → BANNED | VERIFIED | `permanent=True` path (rarely used by connectors) |
| Invalid auth → explicit re-login | PARTIAL | connectors call `mark_account_failed(permanent=False)` → RESTING, not `REQUIRES_LOGIN` |
| Restoration | VERIFIED | `mark_account_restored` RESTING→ACTIVE; API restore endpoint |
| Tests | PARTIAL | OTP activate covered; fail/restore transition tests thin |

---

## P1.7 — OTP login flow hardening

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Registration TTL | VERIFIED | `_REGISTRATION_TTL_SECONDS = 600` |
| One-time use | VERIFIED | `redis.delete` after successful verify |
| Expired/missing token rejected | VERIFIED | code path; **tests missing** |
| Wrong code does not corrupt account | VERIFIED | raise before store; **tests missing** |
| Redis transient failure clear error | MISSING | bare redis calls, no `RubikaLoginError` wrap |
| Encrypted session on success | VERIFIED | `store_channel_session` |
| Activates account | VERIFIED | REQUIRES_LOGIN→ACTIVE |
| Duplicate verify | VERIFIED path | token deleted; **tests missing** |
| Mode/feature gate on API | MISSING | endpoints do not require `user_account` + enabled |
| Secrets not logged | VERIFIED | phone suffix only |
| Happy-path test | VERIFIED | `test_rubika_user_login_flow_success` (mocked rubpy) |

---

## P1.8 — Duplicate session handling

**Status:** VERIFIED (semantics) / PARTIAL (tests)

| Aspect | Status | Evidence |
|--------|--------|----------|
| Latest-row semantics | VERIFIED | `_latest_session_row` / `load_account_session_plaintext` order by `id.desc()` |
| Append on store (no unique constraint) | VERIFIED | `store_channel_session` always inserts |
| Ambiguous load | not an issue | deterministic latest by id |
| Tests | MISSING | no explicit duplicate-row “newest wins” test |

---

## P1.9 — Restart safety

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Encrypted DB persistence | VERIFIED | Fernet + `channel_sessions.ciphertext` |
| OTP state in Redis only (expected) | VERIFIED | design |
| Simulated restart/reload tests | MISSING | no clear caches + new DB session + readiness re-check suite for both modes |

---

## P1.10 — Config consistency

**Status:** PARTIAL (account/session only)

| Setting | core_engine | workers | .env.example | Notes |
|---------|-------------|---------|--------------|-------|
| `RUBIKA_DELIVERY_MODE` | present, **no validator** | present + validator | present | inconsistency to fix |
| `RUBIKA_USER_ACCOUNT_ENABLED` | present | present | present | OK |
| Rate-limit caps/delays | absent (OK) | present | present | out of Phase 1 redesign scope |

Docker compose sets `user_account` on some services — CONFIG_DEPENDENT for runtime mode.

---

## P1.11 — API contract

**Status:** PARTIAL

| Endpoint | Status | Evidence |
|----------|--------|----------|
| `GET .../session/status` | VERIFIED | `accounts.account_session_status` |
| `POST .../session/register` (bot token) | PARTIAL | works; no Rubika mode gate rejecting when `user_account` |
| `POST .../rubika/session/register` (OTP start) | PARTIAL | no mode/enabled gate |
| `POST .../rubika/session/verify` | PARTIAL | no mode/enabled gate |
| Structured errors | PARTIAL | HTTP 400 string `detail`; readiness uses `error` field |
| Frontend compat | VERIFIED | existing response shapes |

---

## P1.12 — Frontend readiness display

**Status:** PARTIAL

| Aspect | Status | Evidence |
|--------|--------|----------|
| Ready / not ready badge | VERIFIED | `ApiTokenSessionPanel` |
| Login required (OTP panel) | PARTIAL | shown when `requires_login` (pool) or manual toggle (accounts) |
| Session missing / invalid / config disabled codes | MISSING | UI does not map readiness `error` codes to distinct labels |
| Wrong mode messaging | MISSING | no delivery_mode display for Rubika |

---

## Summary counts (pre-implementation)

| Status | Count (capabilities) |
|--------|----------------------|
| VERIFIED | 2 (P1.3 largely; P1.8 semantics) |
| PARTIAL | 9 |
| MISSING | 1 (envelope versioning as discrete) |
| BROKEN | 0 whole capability (2 sub-issues: silent mode fallback) |
| CONFIG_DEPENDENT | docker/runtime mode |

## Implementation plan (only non-VERIFIED)

1. Centralize `normalize_rubika_delivery_mode` / `resolve_rubika_delivery_mode`; validate in core_engine Settings; reject invalid in `required_session_type`.
2. Extend `SessionReadiness` + Rubika branches in `evaluate_account_session_readiness` / `build_account_session_status`.
3. Version envelope (v1, backward-compatible).
4. Harden OTP (Redis errors, mode gates, tests).
5. Explicit session-invalid → `REQUIRES_LOGIN`; readiness treats RESTING as not ready.
6. Mode gates on register/OTP APIs.
7. Minimal frontend readiness label mapping.
8. Tests A–L + docs under `docs/audits/rubika_phase1/`.
9. Prefer **NO migration** (latest-row semantics sufficient).
