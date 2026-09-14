# RUBIKA ACCOUNT LIFECYCLE — FORENSIC ARCHITECTURE AUDIT (L0)

**Phase:** L0  
**Date:** 2026-08-29  
**Mode:** READ-ONLY (no production mutation)  
**Scope:** Map current Rubika account/session onboarding lifecycle and defects vs canonical one-active-session model  

**Related live evidence (fleet health re-audit):**
- Fleet size: 43 Rubika accounts
- Proven real-send: Account12, Account79
- Recovered after OTP (auth reconnect pass): 2, 13, 19, 23, 74, 81
- Duplicate session rows: 2, 12, 19, 81, 92
- Decrypt failure: 1 (and 92 under duplicate-review path)
- NO_SESSION_ROW: 33 accounts

---

## 1. Executive summary

The current Rubika **user_account** lifecycle is:

> create Account → OTP start (Redis challenge) → OTP verify (`sign_in`) → **append** encrypted `ChannelSession` → optionally flip `Account.status` to ACTIVE → runtime picks **max(id)** session

This is **not** a canonical one-active-session model.

| Required invariant (remediation) | Current reality |
|---|---|
| Exactly one ACTIVE canonical session per account | No `session_status`; unlimited rows per `(account_id, RUBIKA_SESSION)` |
| READY only after reconnect + identity verify of **persisted** session | READY/ACTIVE after OTP `sign_in` + insert; no post-persist reconnect gate |
| Identity bound Account ↔ Rubika GUID | GUID only inside ciphertext envelope |
| OTP request idempotent | Each start mints a **new** Redis token; prior challenges linger until TTL |
| Relogin preserves working session until replacement proven | Append-only; latest id silently supersedes without validation of new row |
| Single session loader | Multiple independent “latest by id” loaders |
| New READY account auto worker-discovered | Pool discovery exists, but production override pins `RUBIKA_ACCOUNT_IDS=12,79` |
| UI shows lifecycle truth | No in-repo Rubika login UI; docs prompt only |

---

## 2. Delivery modes (context)

| Mode | Session type | Login path |
|---|---|---|
| `user_account` | `SessionType.RUBIKA_SESSION` | OTP register + verify |
| `bot_api` | `SessionType.API_TOKEN` | Token register API |

Primary remediation target: **user_account / RUBIKA_SESSION**.

Sources:
- `core_engine/services/rubika_mode.py`
- Docs: `docs/audits/rubika_phase1/01_ACCOUNT_SESSION_CONTRACT.md` (documents append-only + latest-id as intentional soft contract)

---

## 3. End-to-end current lifecycle map

```
POST /accounts
  → core_engine/api/accounts.py :: create_account
  → Account(platform, phone_number, label, status=ACTIVE by default)

POST /accounts/{id}/rubika/session/register
  → accounts.py :: register_rubika_user_session
  → rubika_mode.assert_rubika_user_login_allowed
  → rubika_user_session.start_rubika_user_login
      → Client.send_code / pass_key challenge
      → Redis SET rubika:user_login:{token} EX 600

POST /accounts/{id}/rubika/session/verify
  → accounts.py :: verify_rubika_user_session
  → rubika_user_session.verify_rubika_user_login
      → Redis GET challenge
      → Client.sign_in(...)
      → hydrate auth/guid/import_key in memory
      → Client.register_device(...)
      → build_session_envelope(...)
      → session_storage.store_channel_session  ★ INSERT new row
      → Account.status ACTIVE if was REQUIRES_LOGIN
      → db.flush(); Redis DEL challenge
      → return success  ★ NO persist-reconnect gate
                    ★ NO Account↔GUID bind
                    ★ NO SUPERSEDE of prior rows

Optional ops enrollment (manual / separate API):
  → POST /rubika/accounts/{id}/pool  :: upsert_rubika_pool_membership
  → RubikaSenderSchedule windows (global) :: resolve_current_phase

Runtime send path:
  → evaluate_rubika_send_preflight
  → load_account_session_plaintext / load_rubika_user_client
      → SELECT ChannelSession WHERE account_id+type ORDER BY id DESC LIMIT 1
  → _connect_authenticated
  → send / resolve recipient GUID (add_address_book) — send path only
```

---

## 4. Step-by-step with exact files/functions

### 4.1 Account creation + metadata

| Concern | Location |
|---|---|
| Create | `core_engine/api/accounts.py` → `create_account` |
| Model | `core_engine/models.py` → `Account` |
| Fields | `platform`, `label`, `phone_number`, `status`, `proxy_url`, `warming_started_at`, … |
| GUID column | **Absent** |
| Default status | Often `ACTIVE` even with no session |

**Defect:** Account can be ACTIVE with `NO_SESSION_ROW`. “Connected” cannot be inferred from `Account.status`.

### 4.2 OTP request

| Concern | Location |
|---|---|
| API | `accounts.py` → `register_rubika_user_session` |
| Service | `rubika_user_session.start_rubika_user_login` |
| Network | rubpy `Client.send_code` (and pass_key stage) |
| Redis key | `rubika:user_login:{registration_token}` |
| TTL | `_REGISTRATION_TTL_SECONDS = 600` |
| State stages | `pass_key` → `code` (stores `phone_code_hash`, RSA keys) |

**Defects:**
- Start is **not idempotent**: each call creates a new token; old keys remain until TTL.
- No account-scoped “one active challenge” lock.
- No explicit resend cooldown beyond provider behavior.
- OTP phone is normalized (`0…` → `98…`) but **not required to equal** `Account.phone_number`.
- Private key material held in Redis for challenge duration (TTL-bound; not in reports).

### 4.3 OTP verification / session generation

| Concern | Location |
|---|---|
| API | `accounts.py` → `verify_rubika_user_session` |
| Service | `rubika_user_session.verify_rubika_user_login` |
| Auth | `client.sign_in(...)` |
| Device | `client.register_device(...)` |
| Envelope | `build_session_envelope` (`phone_number`, `auth`, `guid`, `user_agent`, `private_key`, `version`) |
| Persist | `session_storage.store_channel_session` → encrypt → `db.add(ChannelSession)` |
| Activate account | `Account.status = ACTIVE` if was `REQUIRES_LOGIN` |

**Defects (critical):**
1. Marks login success **without** reconnect using the **persisted ciphertext**.
2. No identity match vs previously bound GUID (none stored on Account).
3. Always **INSERT**; prior sessions remain selectable until newer id exists.
4. Wrong OTP keeps Redis challenge (retry OK) — good — but success path does not transactionalize “prove then activate”.
5. Returns GUID in API response; does not bind it to Account.

### 4.4 Encryption / ChannelSession model

| Concern | Location |
|---|---|
| Encrypt/decrypt | `core_engine/services/crypto.py` via `session_storage.encrypt_session_data` / `decrypt_session_data` |
| Key | `SESSION_SECRET` (Fernet) |
| Store | `session_storage.store_channel_session` — **always insert** |
| Load row | `load_channel_session_plaintext(channel_session)` |

`ChannelSession` fields (relevant): `id`, `account_id`, `session_type`, `ciphertext`, `file_path`, `key_version`, timestamps + Evolution/proxy columns.

**Missing for remediation:** `session_status`, `is_canonical`, `superseded_at`, `rubika_guid`, uniqueness of ACTIVE.

**DB constraints:** `account_id` indexed FK only.  
`RubikaAccountPool` has `UNIQUE(account_id, phase)` — pool is better constrained than sessions.

### 4.5 “Latest session” selection (runtime soft-canonical)

| Site | Behavior |
|---|---|
| `account_session_wiring._latest_session_row` | `ORDER BY id DESC` + session_type |
| `workers/session_access.load_account_session_plaintext` | same |
| `evaluate_account_session_readiness` | uses latest for required type |
| `rubika_health.restore_rubika_account` (one path) | latest **without** type filter — riskier |
| Audit scripts | same pattern |

**Defect:** Newest row wins even if decrypt fails, identity wrong, or structurally invalid relative to an older working row (unless decrypt of latest fails → readiness fail, which can hide a still-valid older session — observed pattern with duplicates / Account1 decrypt issues).

### 4.6 Reconnect (runtime)

| Concern | Location |
|---|---|
| Load client | `workers/connectors/rubika_user.py` → `load_rubika_user_client` |
| Auth connect | `_connect_authenticated` (connect + `decode_auth`/`import_key`; **avoids** `Client.start()` OTP path) |
| Send | `deliver_rubika_user_live` / connector send path |

Reconnect is used for sending and for the fleet health audit.  
**Not** used as a gate inside `verify_rubika_user_login`.

### 4.7 Identity verification

| Layer | What it checks |
|---|---|
| Preflight Layer A | Account exists + platform RUBIKA |
| OTP verify | Uses GUID from `sign_in` result into envelope only |
| Account binding | **None** |
| Fleet audit | Optional post-hoc GUID compare (audit-only; not product gate) |

Recipient GUID caching (`Contact.extra_variables["rubika_guid"]`) is unrelated to sender identity binding.

### 4.8 Pool / schedule / worker coverage

| Concern | Location |
|---|---|
| Pool model | `RubikaAccountPool` + `uq_rubika_pool_account_phase` |
| Pool API | `core_engine/api/rubika.py` → upsert membership |
| Schedule | `RubikaSenderSchedule`; `workers/rubika_account_pool.resolve_current_phase` |
| Multi-account discovery | `workers/rubika_pool_worker.discover_rubika_eligible_account_ids` |
| Coverage heartbeat | `workers/pool_health.publish_account_coverage` / `has_active_worker_coverage` |
| Preflight | `evaluate_rubika_send_preflight` |

**Production compose override (critical ops defect):**

`docker-compose.override.yml` on rubika worker:

```text
RUBIKA_MULTI_ACCOUNT_WORKER=true
RUBIKA_ACCOUNT_IDS=12,79
WORKER_ACCOUNT_ID=12
```

When `RUBIKA_ACCOUNT_IDS` is set, discovery **short-circuits** to that explicit list.  
Newly logged-in pool members are **not** auto-enrolled into worker coverage until env changes / restart with expanded list or empty explicit list.

Refresh loop exists for dynamic discovery — but is defeated by explicit IDs.

### 4.9 READY semantics (current)

| Signal | Meaning today |
|---|---|
| `Account.status == ACTIVE` | Lifecycle flag; may lack session |
| Session readiness `READY` | Latest required-type session decrypts + structure OK |
| Preflight `READY` | Full send gate (pool/schedule/quota/circuit/…) |
| UI “Connected” | No first-class Rubika lifecycle UI in this repo |

There is **no** first-class split of `AUTH_READY` vs `DISPATCH_READY` in product APIs (health audit introduced the separation conceptually only).

---

## 5. Inventory: every ChannelSession write path

### Creates (INSERT)

| Caller | Type | Rubika user relevant |
|---|---|---|
| `session_storage.store_channel_session` | any | **Yes** (helper) |
| `rubika_user_session.verify_rubika_user_login` | `RUBIKA_SESSION` | **Yes** |
| `account_session_wiring.register_api_token_session` | `API_TOKEN` | bot mode |
| WhatsApp / Evolution helpers | other types | No |

### Updates of Rubika ciphertext

**None in production.** Relogin = new row. Evolution/WhatsApp update their own fields only.

### Deletes

No automatic supersede/delete of prior Rubika sessions on login.

---

## 6. Inventory: OTP / auth material generation

| Action | Function |
|---|---|
| Request SMS/code | `start_rubika_user_login` → `send_code` |
| Pass_key stage | same |
| Submit OTP | `verify_rubika_user_login` → `sign_in` |
| RSA keypair for login | `RubikaCrypto.create_keys` during start |
| StringSession hydrate | manual list assign (bypass rubpy `insert` private_key bug) |
| Persist envelope | `build_session_envelope` + `store_channel_session` |

---

## 7. Defect catalog (mapped to remediation phases)

| ID | Defect | Maps to |
|---|---|---|
| D1 | Append-only sessions; no ACTIVE/SUPERSEDED | L1, L2, L6, L7, L15 |
| D2 | Runtime selects max(id), not proven canonical | L1, L9 |
| D3 | No DB unique ACTIVE constraint | L2 |
| D4 | Login success before persist-reconnect prove | L3, L5, L6 |
| D5 | No Account↔GUID identity bind | L8 |
| D6 | OTP start not account-scoped idempotent | L4 |
| D7 | No explicit login state machine on Account | L3, L14 |
| D8 | Relogin can shadow working session with bad latest row | L7 |
| D9 | Multiple independent session loaders | L9 |
| D10 | AUTH vs DISPATCH readiness conflated in ops/UI | L10, L14 |
| D11 | Pool enrollment not automatic on login | L11 |
| D12 | Worker pinned to `RUBIKA_ACCOUNT_IDS=12,79` | L12 |
| D13 | No periodic non-send health reconciler | L13 |
| D14 | No safe duplicate review admin flow | L15 |
| D15 | Historical fleet polluted (duplicates/decrypt/no-session) | L16 |
| D16 | Frontend lacks Rubika lifecycle actions/status | L14 |
| D17 | `restore_rubika_account` may pick latest session without type filter | L9 |
| D18 | Phone on Account may diverge from envelope phone | L5, L8 |

---

## 8. Alignment with live fleet evidence

| Category | IDs (from health audit) | Lifecycle root cause |
|---|---|---|
| VERIFIED_REAL_SEND_OK | 12, 79 | Working sessions exist; 12 still has duplicate rows (latest-id luck) |
| Recovered AUTH pass | 2, 13, 19, 23, 74, 81 | OTP completed; duplicates on 2/19/81 show append-only pollution |
| SESSION_DECRYPT_FAILED | 1 | Latest/only row undecryptable; no canonical fallback |
| MULTIPLE + decrypt | 92 | Historical duplicates + bad material |
| NO_SESSION_ROW | 33 accounts | Login never completed; expected until L19 pilot |

**Conclusion:** Pollution is a systemic consequence of D1–D8, not one-off bad rows.

---

## 9. What already exists that remediation can reuse

| Asset | Reuse |
|---|---|
| Envelope v1 + parse/validate | Keep as session payload contract |
| Fernet `store_channel_session` / decrypt | Keep crypto; add status + upsert/activate semantics around it |
| `_connect_authenticated` (no OTP `start()`) | Reuse for post-login prove + loader/reconciler |
| `evaluate_rubika_send_preflight` | Map to DISPATCH_READY (non-send) |
| `discover_rubika_eligible_account_ids` | Base for L12 once explicit ID pin removed/config-driven |
| Pool unique `(account_id, phase)` | Pattern for session ACTIVE uniqueness |
| Fleet health audit classifications | Seed L13 health snapshot vocabulary |

---

## 10. Non-goals of L0 / hard stops

L0 did **not**:
- mutate production DB/Redis
- delete/supersede sessions
- request/submit OTP
- send messages
- apply schema migrations

Hard stops before later phases (per operator rules):
- production schema migration (L2)
- canonicalizing existing duplicates (L16)
- requesting OTP (L19)
- submitting OTP (L19)
- any real send

---

## 11. Recommended next phase sequence (design-first)

1. **L1 design doc** — canonical session model + status enum + selection rules (no code yet or models-only draft)
2. **L2 migration design** — partial unique index plan + backfill strategy that preserves 12/79
3. **L3–L8 implementation** behind feature flags / isolated tests
4. **L9 loader fence** — single entrypoint; deprecate `_latest_session_row` for Rubika
5. **L10–L13** readiness + discovery + reconciler
6. **L14 UI**
7. **L15–L16** controlled duplicate review + fleet migration reports
8. **L17 hermetic tests**
9. **L18 live RO verification**
10. **L19 one-account OTP pilot** (explicit approval)

---

## 12. L0 acceptance checklist

| Check | Result |
|---|---|
| Full lifecycle traced with files/functions | Yes |
| All ChannelSession create/update paths inventoried | Yes |
| All latest-session selectors inventoried | Yes |
| Defects vs one-canonical-session listed | Yes (D1–D18) |
| Production mutated | **No** |
| Report written | This file |

---

## 13. Phase footer

```
CURRENT_PHASE=L0_FORENSIC_ARCHITECTURE_AUDIT
PHASE_STATUS=COMPLETE
FILES_CHANGED=reports/rubika-remediation/RUBIKA_ACCOUNT_LIFECYCLE_AUDIT.md
TESTS_PASSED=N/A
TESTS_FAILED=N/A
PRODUCTION_MUTATION=False
NEXT_SAFE_ACTION=Operator review L0 → approve L1 canonical session model design (no schema apply / no OTP yet)
```
