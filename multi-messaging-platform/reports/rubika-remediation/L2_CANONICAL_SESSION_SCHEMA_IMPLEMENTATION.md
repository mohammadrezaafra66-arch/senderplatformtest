# L2 — CANONICAL SESSION SCHEMA IMPLEMENTATION

**Phase:** L2 (code + isolated tests only)  
**Date:** 2026-08-29  
**Production migration applied:** **NO**  
**OTP requested:** **NO**  
**Production session/account mutation:** **NO**  
**RUBIKA_ACCOUNT_IDS changed:** **NO** (still `12,79`)  
**Account12/79 production data:** **untouched**

Approved L1 decisions applied in design/code: O1, O4, O6 (deferred unique GUID index), O7, O8, O10.

---

## 1. Files changed

| File | Change |
|---|---|
| `core_engine/models.py` | `RubikaSessionStatus`, `RubikaIdentityStatus`; Account identity fields; ChannelSession lifecycle fields |
| `core_engine/config.py` | `RUBIKA_CANONICAL_SESSION_V1=false`, `AUTO_ENROLL_RUBIKA_POOL=false`, `DEFAULT_RUBIKA_POOL=day` |
| `core_engine/services/rubika_canonical_session.py` | **NEW** — `load_canonical_rubika_session` |
| `core_engine/services/rubika_identity.py` | **NEW** — `bind_or_verify_rubika_identity` |
| `core_engine/services/rubika_session_promotion.py` | **NEW** — `promote_validated_session` (short DB txn only) |
| `core_engine/services/rubika_session_migration_guards.py` | **NEW** — duplicate inventory + refuse auto-canonicalize |
| `core_engine/services/session_storage.py` | Optional `session_status` / `identity_guid` / `login_attempt_id`; Rubika default `LEGACY_UNCLASSIFIED` |
| `core_engine/services/account_session_wiring.py` | Legacy fence warning on `_latest_session_row` for Rubika; `legacy_latest_rubika_session_row` |
| `workers/session_access.py` | Legacy fence warning for Rubika max(id) loads |
| `alembic/versions/rubika_l2_canonical_session_001.py` | **NEW** migration (not applied to production) |
| `tests/core_engine/test_rubika_l2_canonical_session.py` | **NEW** 14 isolated tests |
| `reports/rubika-remediation/L2_CANONICAL_SESSION_SCHEMA_IMPLEMENTATION.md` | This report |

---

## 2. Schema diff (intended)

### `channel_sessions`

| Column | Type | Default / notes |
|---|---|---|
| `session_status` | enum `rubikasessionstatus` | backfill → `legacy_unclassified` |
| `validated_at` | timestamptz | NULL |
| `superseded_at` | timestamptz | NULL |
| `invalidated_at` | timestamptz | NULL |
| `validation_error_code` | varchar(64) | NULL |
| `identity_guid` | varchar(64) | NULL (non-secret identifier) |
| `login_attempt_id` | varchar(64) | NULL |

No `is_canonical` / `is_active` boolean — `session_status` is authoritative.

### `accounts`

| Column | Type | Notes |
|---|---|---|
| `rubika_guid` | varchar(64) | nullable until first verified bind |
| `rubika_identity_verified_at` | timestamptz | |
| `rubika_identity_status` | enum `rubikaidentitystatus` | default `unbound` |

### Enums

**RubikaSessionStatus:**  
`legacy_unclassified`, `pending_login`, `validating`, `active`, `superseded`, `invalid`, `decrypt_failed`, `revoked`

**RubikaIdentityStatus:**  
`unbound`, `verified`, `mismatch_locked`, `operator_override`

### Constraints

```sql
CREATE UNIQUE INDEX uq_channel_sessions_one_active_rubika
  ON channel_sessions (account_id)
  WHERE session_status = 'active'
    AND (session_type = 'rubika_session' OR session_type = 'RUBIKA_SESSION');
```

- Created only after forcing all rows to `legacy_unclassified` (zero ACTIVE).
- Aborts if ACTIVE duplicates already exist.
- **Global unique on `accounts.rubika_guid` NOT created** (O6 — after duplicate inventory).

---

## 3. Migration strategy (written, not applied)

Revision: `rubika_l2_canonical_session_001`  
Down revision: `campaign_accounts_001`

1. Create enums  
2. Add columns with safe defaults  
3. **UPDATE all** `channel_sessions.session_status = legacy_unclassified` (never infer ACTIVE)  
4. Precondition: assert zero ACTIVE rubika sessions  
5. Precondition: assert no multi-ACTIVE groups  
6. Create partial unique index  

**Does not** promote Account12/79.  
**Does not** delete rows.  
**Does not** auto-canonicalize duplicates (`assert_refuses_auto_canonicalize_ambiguous_duplicates`).

Operator must explicitly approve production `alembic upgrade` later.

---

## 4. Canonical loader

`load_canonical_rubika_session(db, account_id, *, require_identity_binding=True)`

| Count ACTIVE | Result |
|---|---|
| 0 | `CanonicalSessionError(NO_ACTIVE_SESSION)` |
| 1 | decrypt + structure validate + return `CanonicalRubikaSession` |
| >1 | `CanonicalSessionError(MULTIPLE_ACTIVE_SESSIONS)` |

No max(id). No LEGACY / VALIDATING load.

---

## 5. Promotion service (O4)

`promote_validated_session(db, account_id=..., candidate_session_id=...)`

- Candidate must be `VALIDATING` with `identity_guid`
- Identity via `bind_or_verify_rubika_identity` (no silent overwrite)
- Short txn: old ACTIVE → SUPERSEDED; candidate → ACTIVE
- **No network** inside promotion
- IntegrityError → `PROMOTION_RACE`

---

## 6. Identity helper

`bind_or_verify_rubika_identity(account, guid)`

| Case | Result |
|---|---|
| unbound | bind → VERIFIED (`IDENTITY_BOUND`) |
| same GUID | `IDENTITY_OK` |
| different GUID | `IDENTITY_MISMATCH`; set `MISMATCH_LOCKED`; **no overwrite** |

---

## 7. Compatibility fence

| Legacy loader | Fence |
|---|---|
| `account_session_wiring._latest_session_row` | warns `event=rubika_legacy_session_loader` for Rubika |
| `workers/session_access.load_account_session_plaintext` | same warning for Rubika |
| `legacy_latest_rubika_session_row` | explicit migrator/audit helper |

`RUBIKA_CANONICAL_SESSION_V1` remains **false** — callers not blindly rewritten.

### Remaining legacy Rubika max(id) callers

| Location | Notes |
|---|---|
| `core_engine/services/account_session_wiring.py` — readiness / has_encrypted_session | still max(id) |
| `workers/session_access.py` | used by `rubika_user.load_rubika_user_client` |
| `workers/connectors/rubika_user.py` | via session_access |
| `workers/connectors/rubika.py` | bot_api token path (API_TOKEN) |
| `core_engine/services/rubika_health.py` | restore path (type filter gap noted in L0) |
| `scripts/_all_rubika_account_health_audit.py` | forensic audit |
| `scripts/_owner_decrypt_audit.py` | forensic |

Future L9 cutover migrates send/readiness paths to canonical loader behind the flag.

---

## 8. Config

| Setting | Default | Meaning |
|---|---|---|
| `RUBIKA_CANONICAL_SESSION_V1` | `false` | Do not force canonical loader yet |
| `AUTO_ENROLL_RUBIKA_POOL` | `false` | Login must not silently pool-enroll (O8) |
| `DEFAULT_RUBIKA_POOL` | `day` | For future L11 when enabled |

---

## 9. Tests (isolated DB `mmp_l2_canonical_test`)

```
14 passed
```

Coverage mapped to L2.9:

1. legacy default LEGACY_UNCLASSIFIED  
2. zero ACTIVE → NO_ACTIVE_SESSION  
3. one ACTIVE → loads  
4. >1 ACTIVE → hard fail  
5. loader never chooses max(id)  
6. legacy not loadable  
7. VALIDATING not loadable  
8. promotion supersedes old ACTIVE  
9. failed promotion leaves old ACTIVE  
10. identity mismatch blocks promotion  
11. Account GUID cannot silently change  
12. partial unique prevents two ACTIVE  
13. 12/79-shaped fixtures stay LEGACY (not auto-promoted)  
14. migration precondition refuses ambiguous duplicate auto-canonicalize  

**Production DB was not used.**

---

## 10. Explicit non-actions

```
L2_PRODUCTION_MIGRATION_APPLIED=False
PRODUCTION_MUTATION=False
OTP_REQUESTED=False
RUBIKA_ACCOUNT_IDS_UNCHANGED=True
ACCOUNT12_79_PRODUCTION_UNTOUCHED=True
AUTO_CANONICALIZATION=False
```

---

## 11. Phase footer

```
L2_SCHEMA_CODE_IMPLEMENTED=True
L2_MIGRATION_WRITTEN=True
L2_PRODUCTION_MIGRATION_APPLIED=False
L2_CANONICAL_LOADER_IMPLEMENTED=True
L2_PROMOTION_SERVICE_IMPLEMENTED=True
L2_IDENTITY_BINDING_HELPER_IMPLEMENTED=True
L2_PARTIAL_UNIQUE_CONSTRAINT_DEFINED=True
L2_LEGACY_BACKFILL_SAFE=True
L2_TESTS_PASSED=14
L2_TESTS_FAILED=0

PRODUCTION_MUTATION=False
OTP_REQUESTED=False

CURRENT_PHASE=L2_CANONICAL_SESSION_SCHEMA_IMPLEMENTATION
PHASE_STATUS=COMPLETE
NEXT_SAFE_ACTION=Review L2 code/tests before any production schema migration
```
