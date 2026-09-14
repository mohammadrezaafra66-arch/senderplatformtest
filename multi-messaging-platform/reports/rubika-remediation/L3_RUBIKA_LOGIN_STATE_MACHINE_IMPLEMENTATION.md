# L3 — RUBIKA LOGIN STATE MACHINE IMPLEMENTATION

**Phase:** L3 (code + isolated tests only)  
**Date:** 2026-08-29  
**Production migration applied:** **NO**  
**Real OTP requested:** **NO**  
**OTP submitted to live Rubika:** **NO**  
**Production session/account mutation:** **NO**  
**Legacy production rows canonicalized:** **NO**  
**RUBIKA_ACCOUNT_IDS changed:** **NO** (still `12,79`)  
**RUBIKA_CANONICAL_SESSION_V1 in production:** **false** (unchanged)

L1/L2 contracts remain authoritative for session status / identity / promotion.

---

## 1. Files changed

| File | Change |
|---|---|
| `core_engine/models.py` | `RubikaLoginChallengeState`, `RubikaLoginChallenge` |
| `core_engine/config.py` | `RUBIKA_OTP_CHALLENGE_TTL_SECONDS=600`, `RUBIKA_OTP_RESEND_COOLDOWN_SECONDS=60` (flag still false) |
| `core_engine/services/rubika_login_state_machine.py` | **NEW** — `request_rubika_login`, `submit_rubika_login_code`, AUTH/DISPATCH split, legacy fence |
| `core_engine/services/rubika_login_fake_provider.py` | **NEW** — fake provider + `PassThroughCandidateProver` for isolated tests |
| `core_engine/services/rubika_login_live_provider.py` | **NEW** — live rubpy adapter (not exercised in L3 tests) |
| `core_engine/services/rubika_user_session.py` | Legacy `start`/`verify` call `assert_legacy_login_allowed()` |
| `core_engine/api/accounts.py` | When flag=true, register/verify route through state machine |
| `alembic/versions/rubika_l3_login_challenge_001.py` | **NEW** migration (revises L2; **not applied to production**) |
| `tests/core_engine/test_rubika_l3_login_state_machine.py` | **NEW** 19 isolated tests |
| `reports/rubika-remediation/L3_RUBIKA_LOGIN_STATE_MACHINE_IMPLEMENTATION.md` | This report |

---

## 2. State machine

```
LOGIN_NOT_STARTED
  → OTP_REQUESTED
  → OTP_WAITING_FOR_OPERATOR
  → OTP_SUBMITTED
  → AUTHENTICATING
  → IDENTITY_VERIFYING
  → SESSION_PERSISTING
  → READY

Failure terminals:
  LOGIN_FAILED
  LOGIN_EXPIRED
  MANUAL_REVIEW_REQUIRED
```

**READY ≠ OTP accepted.** READY requires:

1. Candidate session obtained from provider submit  
2. Envelope structure validated  
3. Candidate persisted as `VALIDATING` (encrypted)  
4. Authenticated reconnect / prove passes  
5. Identity matches Account binding (`bind_or_verify_rubika_identity` via promotion)  
6. Canonical promotion succeeds (`VALIDATING` → `ACTIVE`; prior `ACTIVE` → `SUPERSEDED`)

---

## 3. Challenge schema / model

Table: `rubika_login_challenges`

| Field | Notes |
|---|---|
| `id` | string PK (uuid hex) |
| `account_id` | FK accounts |
| `state` | enum `rubikaloginchallengestate` |
| `provider_challenge_id` | optional provider token |
| `phone_e164` | normalized phone used for OTP |
| `requested_at` / `expires_at` | TTL |
| `last_submit_at` | last submit attempt |
| `attempt_count` | submit attempts |
| `failure_code` | safe code only (no secrets) |
| `candidate_session_id` | VALIDATING session row |
| `completed_session_id` | ACTIVE after READY |
| `created_at` / `updated_at` | |

**OTP code is never stored** (DB columns, audit payloads, or challenge metadata).

### Active states (at most one per account)

`OTP_REQUESTED`, `OTP_WAITING_FOR_OPERATOR`, `OTP_SUBMITTED`, `AUTHENTICATING`, `IDENTITY_VERIFYING`, `SESSION_PERSISTING`

Repeated `request_rubika_login` while active → `OTP_ALREADY_PENDING` (no second challenge).

---

## 4. Authoritative service contracts

### `request_rubika_login(account_id, …)`

1. Account exists + platform=Rubika  
2. Active challenge check (`SELECT … FOR UPDATE`)  
3. Resend cooldown from last `requested_at`  
4. Create challenge → `OTP_REQUESTED`  
5. Provider `request_otp`  
6. Persist provider metadata; ephemeral secret blob in process/injected store (not OTP)  
7. → `OTP_WAITING_FOR_OPERATOR`

Results: `OTP_REQUESTED` | `OTP_ALREADY_PENDING` | `OTP_RATE_LIMITED` | `OTP_REQUEST_FAILED` | `ACCOUNT_NOT_FOUND` | `WRONG_PLATFORM`

No session creation. No `Account.status=ACTIVE` change.

### `submit_rubika_login_code(account_id, challenge_id, code, …)`

1. Challenge belongs to account + row lock  
2. Active / not expired  
3. → `OTP_SUBMITTED` → `AUTHENTICATING`  
4. Provider authenticate  
5. Structure validate envelope  
6. Persist candidate `VALIDATING`  
7. → `IDENTITY_VERIFYING` + prove (reconnect + identity)  
8. → `SESSION_PERSISTING` + `promote_validated_session`  
9. → `READY` only if all gates pass  

Returns `login_state`, `auth_ready`, `dispatch_ready` separately. Does **not** mark dispatch readiness / pool enrollment.

---

## 5. Idempotency strategy

| Scenario | Behavior |
|---|---|
| Repeat request while active | `OTP_ALREADY_PENDING` |
| Resend too soon after prior request | `OTP_RATE_LIMITED` |
| Submit while in-flight states | `OTP_ALREADY_PENDING` (no second candidate) |
| Submit after READY | `LOGIN_ALREADY_COMPLETED` (no new session / rebind / promote) |
| Concurrent submit | `with_for_update` on challenge row + partial unique ACTIVE (L2) |

Ephemeral provider secrets keyed by `challenge_id` in injected `secret_store` (tests) or process default (single-process). Production Redis-backed store is a later hardening item.

---

## 6. Failure codes (safe)

| Code | Meaning |
|---|---|
| `OTP_INVALID` | Provider rejected code |
| `OTP_EXPIRED` | Challenge past `expires_at` |
| `SESSION_STRUCTURE_INVALID` | Envelope parse/build failed |
| `AUTH_RECONNECT_FAILED` | Prove/reconnect failed after OTP OK |
| `IDENTITY_MISMATCH` | Bound GUID ≠ proven identity → `MANUAL_REVIEW_REQUIRED` |
| `SESSION_PROMOTION_FAILED` | Promote refused |
| `OTP_RATE_LIMITED` / `OTP_ALREADY_PENDING` / `OTP_REQUEST_FAILED` | Request-side |

On post-OTP failure: candidate never becomes `ACTIVE`; prior `ACTIVE` untouched; challenge → `LOGIN_FAILED` or `MANUAL_REVIEW_REQUIRED`.

---

## 7. Relogin safety

- Existing `ACTIVE` remains runtime session during candidate flow.  
- Failed candidate → `INVALID` / challenge failed; old `ACTIVE` stays `ACTIVE`.  
- Successful promote: old `ACTIVE` → `SUPERSEDED`, new → `ACTIVE` (L2 atomic promote).

---

## 8. LOGIN vs AUTH vs DISPATCH

| Concept | Source |
|---|---|
| `LOGIN_STATE` | `RubikaLoginChallenge.state` (`READY` after full gates) |
| `AUTH_READY` | `load_canonical_rubika_session(..., require_identity_binding=True)` |
| `DISPATCH_READY` | Readonly pool/schedule/coverage check — **never** auto-fixed by login |

With `AUTO_ENROLL_RUBIKA_POOL=false`, successful login typically yields:

`LOGIN_STATE=READY`, `AUTH_READY=True`, `DISPATCH_READY=False`  
(e.g. `NOT_IN_POOL` / `NO_SCHEDULE` / `NO_WORKER_CONSUMER`) — **not** a login failure.

---

## 9. Legacy fencing

| Path | Flag=false | Flag=true |
|---|---|---|
| `start_rubika_user_login` / `verify_rubika_user_login` | Allowed (legacy) | `RubikaLoginError` via `assert_legacy_login_allowed` |
| API `…/rubika/session/register|verify` | Legacy services | `request_rubika_login` / `submit_rubika_login_code` |

Compatibility not removed. Production keeps flag **false**.

**Note:** When flag=true, API verify currently injects `PassThroughCandidateProver` as a temporary bridge until live reconnect prover is wired (L9/L19). Isolated L3 tests use a proving path that fails reconnect/identity correctly.

---

## 10. Feature flag

```
RUBIKA_CANONICAL_SESSION_V1=false   # production default — do not enable
AUTO_ENROLL_RUBIKA_POOL=false
RUBIKA_OTP_CHALLENGE_TTL_SECONDS=600
RUBIKA_OTP_RESEND_COOLDOWN_SECONDS=60
```

Isolated tests force flag=true + fake provider + isolated DB `mmp_l3_login_test`.

---

## 11. Tests (isolated)

**Harness:** temp DB `mmp_l3_login_test`, `create_all`, Fernet `SESSION_SECRET`, fake Rubika provider.  
**No live OTP. No production DB writes.**

| # | Coverage | Result |
|---|---|---|
| 1 | First OTP creates one challenge | PASS |
| 2 | Repeat → `OTP_ALREADY_PENDING` | PASS |
| 3 | Resend cooldown | PASS |
| 4 | Expired rejected | PASS |
| 5 | Wrong challenge/account | PASS |
| 6 | OTP never persisted | PASS |
| 7 | Success → VALIDATING then ACTIVE | PASS |
| 8–9 | Reconnect required; failure ≠ READY | PASS |
| 10 | Identity mismatch blocks ACTIVE | PASS |
| 11 | Failed candidate preserves old ACTIVE | PASS |
| 12 | Successful relogin supersedes | PASS |
| 13 | Repeat successful submit idempotent | PASS |
| 14 | Concurrent submit → one ACTIVE | PASS |
| 15–17 | READY / AUTH_READY / DISPATCH independent | PASS |
| 18 | No auto pool when flag false | PASS |
| 19 | No worker restart in login module | PASS |
| 20 | Legacy fenced when canonical=true (+ allowed when false) | PASS |

**L3_TESTS_PASSED=19** (checklist items covered; some combined in one test)  
**L3_TESTS_FAILED=0**

---

## 12. Remaining blockers before production migration / live OTP

1. **Apply L2 then L3 alembic** only after operator review (not done).  
2. **Wire live `CandidateSessionProver`** (authenticated reconnect) into API path — replace PassThrough before any live pilot.  
3. **Redis (or durable) secret_store** for multi-process OTP handshake secrets.  
4. **Partial unique index / one-active-challenge** enforcement at DB level (app currently enforces via query + lock).  
5. **L16** historical duplicate canonicalization (operator-approved).  
6. **Do not** clear `RUBIKA_ACCOUNT_IDS` or enable flag in production until L12/L19 gates.  
7. **No live OTP pilot** until L3 review + schema migration + live prover.

---

## 13. Safety confirmation

| Guard | Status |
|---|---|
| Production migration applied | False |
| Production mutation | False |
| Real OTP requested | False |
| Live Rubika OTP submit | False |
| Flag enabled in production | False |
| RUBIKA_ACCOUNT_IDS removed | False |
