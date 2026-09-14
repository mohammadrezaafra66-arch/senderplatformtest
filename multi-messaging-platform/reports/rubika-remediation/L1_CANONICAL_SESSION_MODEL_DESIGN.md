# L1 — CANONICAL RUBIKA SESSION MODEL DESIGN

**Phase:** L1 (design only)  
**Date:** 2026-08-29  
**Authority:** L0 forensic audit (`RUBIKA_ACCOUNT_LIFECYCLE_AUDIT.md`) + fleet health re-audit  
**Mode:** NO schema apply · NO migration · NO OTP · NO session mutation · NO change to Account12/79 · NO removal of `RUBIKA_ACCOUNT_IDS`

This document is the implementation contract for L2+. Ambiguity here is a defect.

---

## 0. Scope and non-goals

**In scope:** Semantics, invariants, proposed schema, loader/login contracts, legacy mapping, migration preconditions, open operator decisions.

**Out of scope (this phase):** Alembic migrations, code that writes production data, OTP request/submit, canonicalization of live duplicates, worker env changes, UI implementation.

**Delivery mode:** `RUBIKA_DELIVERY_MODE=user_account` / `SessionType.RUBIKA_SESSION` only.  
`bot_api` / `API_TOKEN` remains a separate track (may later mirror patterns).

---

## 1. Explicit invariants

```
I1  ONE_ACTIVE_SESSION
    ∀ Rubika Account a:
      |{ s ∈ ChannelSession | s.account_id = a.id
           ∧ s.session_type = RUBIKA_SESSION
           ∧ s.session_status = ACTIVE }| ∈ {0, 1}

I2  RUNTIME_LOADS_ACTIVE_ONLY
    Workers, preflight session layer, connectors, reconciler
    load ONLY ACTIVE. Never max(id). Never “latest wins”.

I3  ACTIVE_MEANS_PROVEN
    ACTIVE ⇒ decrypt OK ∧ structure OK ∧ reconnect PASS
             ∧ identity match vs Account.rubika_guid
             ∧ is the sole canonical runtime session

I4  IDENTITY_IMMUTABLE_BY_DEFAULT
    Account.rubika_guid once set is not silently overwritten.
    Mismatch ⇒ block activation (IDENTITY_MISMATCH).

I5  RELOGIN_SAFE_SWAP
    Old ACTIVE remains ACTIVE until candidate is proven;
    then atomic ACTIVE→SUPERSEDED and candidate→ACTIVE.
    Candidate failure leaves old ACTIVE untouched.

I6  AUTH_≠_DISPATCH
    Login/session success ⇒ AUTH_READY at most.
    DISPATCH_READY requires separate operational gates.

I7  NO_DELETE_ON_ROTATION
    SUPERSEDED / INVALID / DECRYPT_FAILED / REVOKED rows are retained
    for forensic review unless a later approved archive policy says otherwise.

I8  PROTECTED_ACCOUNTS
    Account12 and Account79 working sessions must remain usable through
    every migration step (no premature unique-index apply that fails mid-fleet).
```

---

## 2. State diagrams

### 2.1 Session status diagram

```
                    ┌─────────────────┐
                    │  PENDING_LOGIN  │  (row reserved / challenge linked; rare for ciphertext rows)
                    └────────┬────────┘
                             │ OTP accepted + envelope written (not yet proven)
                             ▼
                    ┌─────────────────┐
         fail ──────│   VALIDATING    │────── fail ──► INVALID / DECRYPT_FAILED
         │          └────────┬────────┘
         │                   │ decrypt+structure+reconnect+identity PASS
         │                   ▼
         │          ┌─────────────────┐
         │          │     ACTIVE      │◄──── only one per account (I1)
         │          └────────┬────────┘
         │                   │ atomic promotion of newer proven candidate
         │                   ▼
         │          ┌─────────────────┐
         └─────────►│   SUPERSEDED    │  retained
                    └─────────────────┘

        ACTIVE ──(operator revoke / security)──► REVOKED
        VALIDATING / ACTIVE ──(decrypt broken later)──► DECRYPT_FAILED
        any non-terminal ──(manual invalidate)──► INVALID
```

Terminal-ish retained states: `SUPERSEDED`, `INVALID`, `DECRYPT_FAILED`, `REVOKED`.  
`PENDING_LOGIN` / `VALIDATING` are transient candidate states.

### 2.2 Login challenge / account login diagram

```
LOGIN_NOT_STARTED
    │ request OTP (idempotent / cooldown)
    ▼
OTP_REQUESTED ──► OTP_WAITING_FOR_OPERATOR
    │ submit code
    ▼
OTP_SUBMITTED
    ▼
AUTHENTICATING          (sign_in / in-memory auth)
    ▼
IDENTITY_VERIFYING      (GUID vs Account binding)
    ▼
SESSION_PERSISTING      (write VALIDATING row + prove + promote)
    ▼
READY                   (AUTH_READY; session ACTIVE)
    │
    ├─ timeout / TTL ──► LOGIN_EXPIRED
    ├─ auth/OTP fail ──► LOGIN_FAILED
    └─ GUID conflict ──► MANUAL_REVIEW_REQUIRED
```

**Ownership:**
- Challenge states live on **login challenge** (Redis now → durable table later optional).
- Session states live on **ChannelSession.session_status**.
- Account exposes derived **login_status** / health snapshot — not a second source of truth for session ciphertext.

---

## 3. Session state table (L1.1)

| State | Meaning | Runtime load? | Worker use? | Reconnect probe? | Transition-in | Transition-out | Historical retained? |
|---|---|---|---|---|---|---|---|
| **PENDING_LOGIN** | Placeholder / challenge-linked row before ciphertext exists, or reserved slot | No | No | No | Login started; optional row create | → VALIDATING when envelope written | Yes |
| **VALIDATING** | Ciphertext stored; awaiting decrypt/structure/reconnect/identity prove | No (except the validation job itself) | No | Yes (validator only) | Envelope persisted post-OTP | → ACTIVE on prove; → INVALID / DECRYPT_FAILED on fail | Yes |
| **ACTIVE** | Sole proven canonical session | **Yes** | **Yes** | **Yes** | Proven validation + atomic promote | → SUPERSEDED (rotation); → REVOKED; → DECRYPT_FAILED (later heal fail) | Yes |
| **SUPERSEDED** | Formerly ACTIVE; replaced by newer proven ACTIVE | No | No | No (forensic only) | Atomic promotion of successor | → REVOKED (rare) | **Yes** |
| **INVALID** | Structurally bad / failed validation / policy reject | No | No | No | Validation fail (non-decrypt) or operator invalidate | Usually terminal | **Yes** |
| **DECRYPT_FAILED** | Ciphertext present but cannot decrypt with current keys | No | No | No | Decrypt fail at validate or reconcile | → INVALID / operator repair path | **Yes** |
| **REVOKED** | Explicit operator/security kill | No | No | No | Operator revoke | Terminal | **Yes** |

### ACTIVE definition (normative)

A session may be `ACTIVE` only if all are true:

1. Encrypted payload exists (`ciphertext` non-empty)
2. Decrypt succeeds with current key material
3. Structure valid (`parse_session_envelope` / user-account contract)
4. Authenticated reconnect passes (`_connect_authenticated` + own-identity probe)
5. Returned Rubika identity matches `Account.rubika_guid` (after binding rules)
6. It is the **only** `ACTIVE` `RUBIKA_SESSION` for that account

---

## 4. Canonical invariant (L1.2)

```
load_canonical_rubika_session(account_id):

  rows = SELECT * FROM channel_sessions
         WHERE account_id = :id
           AND session_type = 'rubika_session'   -- enum value as stored
           AND session_status = 'ACTIVE'

  if len(rows) == 0: raise NO_ACTIVE_SESSION
  if len(rows)  > 1: raise MULTIPLE_ACTIVE_SESSIONS   -- hard fail; no pick
  return validate(rows[0])  -- decrypt/structure/identity checks as configured
```

**Forbidden forever for Rubika user_account runtime:**

- `ORDER BY id DESC LIMIT 1` as selection of “current”
- “Any decryptable row”
- Silent preference among duplicates

Compatibility shims during migration may **read** legacy unclassified rows only inside an explicit migrator/admin tool — never in worker send path after cutover flag.

---

## 5. Account identity binding (L1.3)

### 5.1 Recommended Account fields (minimal)

| Field | Type | Null | Purpose |
|---|---|---|---|
| `rubika_guid` | `VARCHAR(64)` | YES until first verified login | Stable provider identity |
| `rubika_identity_verified_at` | `TIMESTAMPTZ` | YES | When binding was confirmed |
| `rubika_identity_status` | enum/text | YES / default `UNBOUND` | `UNBOUND` \| `VERIFIED` \| `MISMATCH_LOCKED` \| `OPERATOR_OVERRIDE` |

Optional audit (preferred as append-only log table, not churning Account columns):

| Table | Purpose |
|---|---|
| `rubika_identity_events` | `account_id`, `event`, `old_guid`, `new_guid`, `actor`, `reason`, `created_at` |

Do **not** store auth/private_key on Account.

### 5.2 Binding rules

| Event | Behavior |
|---|---|
| First successful prove (VALIDATING→ACTIVE) and `rubika_guid IS NULL` | **Bind** GUID from envelope/probe; set `VERIFIED` + timestamp; emit audit event |
| Subsequent login GUID == bound | Allow promotion |
| Subsequent login GUID ≠ bound | **Do not activate**; candidate → `INVALID`; account login → `MANUAL_REVIEW_REQUIRED`; `IDENTITY_MISMATCH`; optionally set `MISMATCH_LOCKED` |
| Automatic change | **Forbidden** |
| Operator override | Explicit admin action with reason + dual confirmation; writes audit event; may set `OPERATOR_OVERRIDE` then re-bind; never silent |

### 5.3 Phone vs GUID

- `Account.phone_number` remains operator label / login input hint.
- Canonical identity for Rubika is **GUID**, not phone digits.
- Envelope phone may be stored encrypted in session only; mismatch with `Account.phone_number` is a **warning**, not automatic rebind (open decision O3).

---

## 6. Candidate session / relogin promotion (L1.4 / L1.8 algorithm)

### 6.1 Relogin promotion algorithm (normative)

```
Given account A with optional old_active = ACTIVE session (may be none)

1. OTP challenge completes AUTHENTICATING successfully (in-memory).
2. IDENTITY_VERIFYING:
     if A.rubika_guid set and guid != A.rubika_guid → abort (no row ACTIVE change)
3. SESSION_PERSISTING:
     BEGIN
       INSERT ChannelSession(..., session_status=VALIDATING, identity_guid=guid,
                              login_attempt_id=challenge_id)
       # old_active remains ACTIVE
     COMMIT  -- candidate durable before prove (or keep in same txn if prove is short;
             -- prefer commit candidate then prove to avoid long locks; see O4)

4. Prove candidate (outside or carefully inside txn):
     decrypt → structure → reconnect → identity match

5. If prove FAIL:
     UPDATE candidate SET session_status=INVALID|DECRYPT_FAILED,
                          validation_error_code=...
     old_active UNCHANGED
     return LOGIN_FAILED / MANUAL_REVIEW_REQUIRED

6. If prove PASS:
     BEGIN
       -- re-check I1
       SELECT … FOR UPDATE account/sessions
       UPDATE old_active SET session_status=SUPERSEDED, superseded_at=now()
         WHERE id=old_active.id AND session_status=ACTIVE
       UPDATE candidate SET session_status=ACTIVE, validated_at=now()
         WHERE id=candidate.id AND session_status=VALIDATING
       -- assert exactly one ACTIVE
     COMMIT

7. Derived AUTH_READY=true; DISPATCH_READY evaluated separately.
```

**Critical:** Never delete old ACTIVE first. Never mark candidate ACTIVE before prove.

### 6.2 First login (no prior ACTIVE)

Same algorithm with `old_active = None`; step 6 only promotes candidate → ACTIVE and binds GUID if unbound.

---

## 7. Proposed schema (L1.5) — minimal clean model

### 7.1 `channel_sessions` additions (Rubika-relevant)

| Column | Type | Default / null | Notes |
|---|---|---|---|
| `session_status` | enum/text | **`LEGACY_UNCLASSIFIED`** for backfill | Required after migration |
| `validated_at` | timestamptz | NULL | Set on ACTIVE promotion |
| `superseded_at` | timestamptz | NULL | Set when leaving ACTIVE via rotation |
| `invalidated_at` | timestamptz | NULL | INVALID / REVOKED / DECRYPT_FAILED |
| `validation_error_code` | varchar(64) | NULL | Safe code only (no secrets) |
| `identity_guid` | varchar(64) | NULL | Denormalized from envelope for indexing/review (not secret) |
| `login_attempt_id` | varchar(64) | NULL | Ties to challenge token / attempt id |

**Rejected as redundant:** `is_canonical` boolean (ACTIVE uniqueness is enough).

Evolution/WhatsApp columns remain untouched; `session_status` applies primarily to `RUBIKA_SESSION` (other types may stay NULL or `N/A` — open decision O5).

### 7.2 `accounts` additions

As in §5.1: `rubika_guid`, `rubika_identity_verified_at`, `rubika_identity_status`.

### 7.3 Optional `rubika_login_attempts` (recommended for L3–L5, not blocking L2)

Durable challenge metadata **without OTP codes**:

`id`, `account_id`, `registration_token_hash`, `stage`, `status`, `requested_at`, `expires_at`, `resend_count`, `last_error_code`

Redis may remain the hot path initially; DB attempt rows make UI/audit truthful.

### 7.4 Constraints / indexes (design only — do not apply in L1)

```sql
-- Conceptual (PostgreSQL)
CREATE UNIQUE INDEX uq_channel_sessions_one_active_rubika
  ON channel_sessions (account_id)
  WHERE session_type = 'rubika_session'   -- exact enum storage form TBD in L2
    AND session_status = 'ACTIVE';

CREATE INDEX ix_channel_sessions_account_type_status
  ON channel_sessions (account_id, session_type, session_status);

CREATE UNIQUE INDEX uq_accounts_rubika_guid
  ON accounts (rubika_guid)
  WHERE rubika_guid IS NOT NULL;
  -- Open decision O6: enforce global uniqueness of GUID across accounts
```

**Nullable strategy:** Historical rows backfilled to `LEGACY_UNCLASSIFIED` (not ACTIVE) so partial unique index can be applied **before** any ACTIVE is assigned — zero ACTIVE initially, then controlled promotion.

### 7.5 Enum storage note

Today Postgres uses SQLAlchemy `Enum(SessionType)` / string values historically mixed (`rubika_session` vs `RUBIKA_SESSION` in dumps). L2 must pin exact DB representation before writing the partial index predicate. **Open decision O1.**

---

## 8. Legacy compatibility (L1.6)

### 8.1 Staging status

Introduce **`LEGACY_UNCLASSIFIED`** as migration-only session status:

| Meaning | Runtime load? | Worker? |
|---|---|---|
| Pre-model historical row; not yet reviewed | **No** (after cutover) | **No** |

During compatibility window (feature flag `RUBIKA_CANONICAL_SESSION_V1=false`):

- Old loaders may still use max(id) **only if flag off**
- New loader refuses LEGACY rows

After cutover flag on: LEGACY rows invisible to runtime until admin `SET_CANONICAL` / migrator promotes one proven row to ACTIVE (or marks INVALID / DECRYPT_FAILED).

### 8.2 Mapping rules (no deletes)

| Current evidence | Initial mapped status |
|---|---|
| No ChannelSession | (no row) → account login `LOGIN_NOT_STARTED` |
| Row decrypt fails | `DECRYPT_FAILED` or keep `LEGACY_UNCLASSIFIED` + error code until classified |
| Row decrypt+structure OK, not yet proven reconnect | `LEGACY_UNCLASSIFIED` (do **not** auto-ACTIVE) |
| Duplicate decryptable rows | All `LEGACY_UNCLASSIFIED` until L16 review |
| Account12 / Account79 proven working | Still `LEGACY_UNCLASSIFIED` until **protected** L16 step explicitly promotes the chosen row to ACTIVE after RO reconnect prove |

### 8.3 Protection of Account12 / Account79

1. Migration playbook names them `PROTECTED_CANONICAL_CANDIDATES`.
2. Partial unique index applied only when **no account has >0 ACTIVE** (all LEGACY first) — index is safe.
3. First ACTIVE promotions for 12/79 require:
   - RO reconnect PASS
   - identity capture/bind
   - explicit operator approval checklist
4. No bulk job may SUPERSEDE their working ciphertext.
5. Feature flag cutover for workers only after 12/79 have ACTIVE.

**No automatic canonicalization in L1–L2 apply.** L16 owns selection.

---

## 9. Canonical loader contract (L1.7)

### `load_canonical_rubika_session(account_id: int) -> CanonicalRubikaSession`

**Inputs**

| Arg | Meaning |
|---|---|
| `account_id` | MMP Account PK |
| `db` | SQLAlchemy Session |
| `*, require_identity_binding: bool = True` | Fail if Account GUID unbound |
| `*, probe_reconnect: bool = False` | Default false for hot path; true for reconciler/login prove |

**Query semantics**

```
SELECT … FROM channel_sessions
 WHERE account_id=:id AND session_type=RUBIKA_SESSION AND session_status='ACTIVE'
 FOR SHARE  -- optional; document in L2
```

Exactly 0 or 1 row. **No ORDER BY id.**

**Outputs (typed)**

| Field | Content |
|---|---|
| `account_id` | int |
| `session_id` | int |
| `envelope` | parsed fields needed to build client (in-memory only) |
| `identity_guid` | str |
| `validated_at` | datetime \| None |

Never return raw ciphertext in API responses.

**Error codes**

| Code | When |
|---|---|
| `NO_ACTIVE_SESSION` | 0 ACTIVE rows |
| `MULTIPLE_ACTIVE_SESSIONS` | >1 ACTIVE (invariant break; page ops) |
| `SESSION_DECRYPT_FAILED` | ACTIVE row cannot decrypt |
| `SESSION_STRUCTURALLY_INVALID` | envelope invalid |
| `IDENTITY_BINDING_MISSING` | require_identity_binding and Account.rubika_guid null |
| `IDENTITY_MISMATCH` | envelope/probe GUID ≠ Account.rubika_guid |

On decrypt/structure failure of an ACTIVE row: reconciler should move row to `DECRYPT_FAILED` / `INVALID` and clear ACTIVE (controlled), yielding `NO_ACTIVE_SESSION` thereafter — **not** fall back to another row.

**Caching**

- Default: **no cross-request cache** of plaintext.
- Optional short-lived process cache of `session_id` only; always re-decrypt from DB for send.
- Invalidate on promotion/revoke events.

**Callers (post-cutover must use this only)**

- `load_rubika_user_client`
- session readiness / preflight Layer D
- health reconciler
- login promotion prove

Fence: `_latest_session_row` for Rubika becomes legacy/migrator-only.

---

## 10. Login state machine contract (L1.8)

### 10.1 Where state lives

| State family | Owner | Persistence |
|---|---|---|
| Challenge / OTP flow | Login attempt | Redis (± future `rubika_login_attempts`) |
| Session ciphertext lifecycle | `ChannelSession.session_status` | Postgres |
| Derived account UX | Account health snapshot / `login_status` view | Derived or cached column |

**Do not** overload `Account.status` (ACTIVE/RESTING/BANNED/REQUIRES_LOGIN) as the OTP state machine. Keep existing AccountStatus for send lifecycle; add separate `rubika_login_status` (derived or column) for UX.

### 10.2 Login state table

| State | Owner | Meaning | Typical next |
|---|---|---|---|
| `LOGIN_NOT_STARTED` | Account derived | No ACTIVE; no open challenge | OTP request |
| `OTP_REQUESTED` | Challenge | Provider call issued | WAITING |
| `OTP_WAITING_FOR_OPERATOR` | Challenge | Challenge stored; awaiting code | SUBMITTED / EXPIRED |
| `OTP_SUBMITTED` | Challenge | Code accepted by API layer | AUTHENTICATING |
| `AUTHENTICATING` | Challenge | `sign_in` in progress | IDENTITY_VERIFYING / FAILED |
| `IDENTITY_VERIFYING` | Challenge | GUID checks | SESSION_PERSISTING / MANUAL_REVIEW |
| `SESSION_PERSISTING` | Challenge+Session | VALIDATING row + prove + promote | READY / FAILED |
| `READY` | Account derived | `AUTH_READY` (ACTIVE session proven) | (idle) |
| `LOGIN_FAILED` | Challenge | Retryable/non-retryable failure | NOT_STARTED / WAITING |
| `LOGIN_EXPIRED` | Challenge | TTL elapsed | NOT_STARTED |
| `MANUAL_REVIEW_REQUIRED` | Account derived | Identity mismatch / multi-active / etc. | operator |

OTP codes: never persisted.

---

## 11. Readiness semantics (L1.9)

| Flag | Requires |
|---|---|
| **AUTH_READY** | Exactly one ACTIVE session ∧ decrypt/structure OK ∧ (reconnect OK if last probe fresh per policy) ∧ identity binding VERIFIED ∧ match |
| **DISPATCH_READY** | AUTH_READY ∧ Account.enabled/ACTIVE ∧ pool membership for current phase ∧ schedule applicable ∧ worker coverage heartbeat ∧ not paused ∧ kill switch off ∧ circuit allows ∧ (quota gates when evaluated) |

Login success ⇒ at most **AUTH_READY**.  
Missing worker coverage ⇒ `AUTH_READY=true`, `DISPATCH_READY=false`, block=`NO_WORKER_CONSUMER` (or similar) — **not** login failure.

Note: Production still pins `RUBIKA_ACCOUNT_IDS=12,79` (untouched in L1). Design acknowledges DISPATCH_READY will stay false for other AUTH_READY accounts until L12 removes/relaxes that pin under separate approval.

---

## 12. Migration preconditions (L1.10)

Before L2 schema implementation / apply:

| # | Precondition | Owner |
|---|---|---|
| P1 | Verified DB backup / snapshot of `accounts`, `channel_sessions` | Ops |
| P2 | Fresh duplicate inventory (account_id → session_ids) from RO query | Engineering |
| P3 | Protected list locked: Account **12**, **79** | Operator sign-off |
| P4 | Canonical **candidate** evidence for 12/79 (session ids, reconnect RO proof) — selection not applied yet | Engineering |
| P5 | Enum storage form confirmed in live Postgres (`sessiontype` / check values) | Engineering |
| P6 | Rollback plan: drop new columns / leave LEGACY; feature flag off restores max(id) temporarily | Engineering |
| P7 | Partial unique index rollout sequence: (a) add nullable columns (b) backfill LEGACY_UNCLASSIFIED (c) create unique index (0 ACTIVE) (d) compatibility layer (e) controlled ACTIVE promotion (L16) (f) cutover flag | Engineering |
| P8 | Compatibility layer design: dual-read behind `RUBIKA_CANONICAL_SESSION_V1` | Engineering |
| P9 | No OTP / no worker `RUBIKA_ACCOUNT_IDS` change in L2 apply window | Operator |
| P10 | Hermetic tests for loader/invariant exist before cutover (L17 subset can start after L2 models land in code) | Engineering |

**Rollback design (sketch):**

1. Set feature flag off → old loaders.
2. Do not delete SUPERSEDED rows.
3. If index blocks emergency insert, only ops-approved temporary drop of partial index (documented incident).

---

## 13. Compatibility layer (for L2+ implementers)

```
if settings.RUBIKA_CANONICAL_SESSION_V1:
    return load_canonical_rubika_session(account_id)
else:
    return legacy_latest_rubika_session(account_id)  # current max(id); deprecated
```

Logging: every legacy path hit emits `event=rubika_legacy_session_loader` for burn-down.

---

## 14. Open decisions requiring operator approval

| ID | Decision | Recommendation | Impact |
|---|---|---|---|
| **O1** | Exact Postgres enum/string predicate for `session_type` in unique index | Inspect live DB; freeze one form | Blocks L2 index SQL |
| **O2** | Persist `identity_guid` in clear on `channel_sessions` | Yes — GUID is identifier, not auth secret | Review UI / indexes |
| **O3** | Envelope phone ≠ Account.phone_number | Warn only; GUID is identity | Avoid false IDENTITY_MISMATCH |
| **O4** | Prove candidate inside one DB transaction vs commit-then-prove | Commit VALIDATING then prove then promote txn | Lock duration vs crash windows |
| **O5** | `session_status` for non-Rubika session types | NULL = not applicable | Avoid WhatsApp/Evolution breakage |
| **O6** | Global unique `accounts.rubika_guid` | Yes, partial unique where not null | Prevents two MMP accounts sharing one Rubika identity |
| **O7** | When to clear `RUBIKA_ACCOUNT_IDS=12,79` | Separate L12 approval after AUTH_READY fleet subset | DISPATCH_READY for recovered accounts |
| **O8** | Auto-enroll new ACTIVE into pool phase `day`? | Config flag default **false** until policy confirmed | L11 |
| **O9** | Reconciler reconnect frequency | Conservative (e.g. hourly / on-demand) | Load on Rubika API |
| **O10** | L16 auto-suggest canonical among LEGACY duplicates | Suggest only; operator confirms SET_CANONICAL | Safety |

---

## 15. Explicit non-actions in L1

- SCHEMA_APPLIED = False  
- No Alembic revision created as “applied”  
- No production column writes  
- No OTP  
- No session status updates on live rows  
- Account12/79 untouched  
- `RUBIKA_ACCOUNT_IDS` retained as-is  

---

## 16. Design acceptance (L1)

| Deliverable | Status |
|---|---|
| State diagrams | §2 |
| Session state table | §3 |
| Login state table | §10 |
| Account identity-binding model | §5 |
| Proposed schema | §7 |
| Constraints/indexes | §7.4 |
| Canonical loader contract | §9 |
| Relogin promotion algorithm | §6 |
| Legacy compatibility | §8 |
| Migration preconditions | §12 |
| Invariants | §1 |
| Open decisions | §14 |

---

## 17. Phase footer

```
L1_CANONICAL_MODEL_DEFINED=True
ONE_ACTIVE_SESSION_INVARIANT_DEFINED=True
IDENTITY_BINDING_DEFINED=True
RELOGIN_PROMOTION_DEFINED=True
CANONICAL_LOADER_CONTRACT_DEFINED=True
LEGACY_COMPATIBILITY_DEFINED=True
SCHEMA_APPLIED=False
PRODUCTION_MUTATION=False
OTP_REQUESTED=False

CURRENT_PHASE=L1_CANONICAL_SESSION_MODEL_DESIGN
PHASE_STATUS=COMPLETE
FILES_CHANGED=reports/rubika-remediation/L1_CANONICAL_SESSION_MODEL_DESIGN.md
TESTS_PASSED=N/A
TESTS_FAILED=N/A
NEXT_SAFE_ACTION=Review L1 design (especially open decisions O1–O10) before any schema implementation
```
