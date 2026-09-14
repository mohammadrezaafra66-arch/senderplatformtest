# L4 — REAL PROVER AND MIGRATION REHEARSAL

**Date:** 2026-08-31  
**Status:** COMPLETE (isolated rehearsal + final evidence reconciliation)  
**Production migration applied:** NO  
**Production `alembic_version`:** `campaign_accounts_001` (unchanged)  
**Rehearsal `alembic_version`:** `rubika_l3_login_challenge_001`

---

## Final evidence reconciliation (2026-08-31, read-only production)

**Discrepancy classification:** `C` — previous transcription/reporting error (not production ownership change; not latest L4 artifact error).

An earlier blocked-phase assistant summary incorrectly stated:

- Account12 sessions = `600,657`
- Account79 session = `728`

That came from mis-associating interleaved `psql` lines. It does **not** match production, the fleet health audit, or `L4_MIGRATION_REHEARSAL.json`.

### Authoritative production ownership (queried `mmp_db` 2026-08-31)

| account_id | session_id | session_type | created_at (UTC) | decrypt_status | structural_status |
|---|---|---|---|---|---|
| 12 | 657 | RUBIKA_SESSION | 2026-08-25 12:22:46 | OK (health audit + ciphertext present) | OK |
| 12 | 728 | RUBIKA_SESSION | 2026-08-27 11:34:51 | OK | OK |
| 79 | 600 | RUBIKA_SESSION | 2026-08-24 10:06:44 | OK | OK |

Independent session→account lookup:

| session_id | account_id |
|---|---|
| 600 | **79** |
| 657 | **12** |
| 728 | **12** |

Cross-check: `ALL_RUBIKA_ACCOUNT_HEALTH_AUDIT.json` already recorded account 12 → `[728,657]`, account 79 → `[600]`.

### Real-send evidence (read-only `message_attempts` SUCCESS)

| account_id | success_attempt_count | attempt_ids |
|---|---|---|
| 12 | 2 | 2, 15 |
| 79 | 1 | 17 |

### Migration safety precheck (production)

| Check | Value |
|---|---|
| TOTAL_RUBIKA_ACCOUNTS | 43 |
| Account12/79 session rows | preserved as above |
| `session_status` column on production | **absent** (L2 not applied) |
| Canonical ACTIVE sessions on production | **0** (column does not exist; no ACTIVE) |
| `alembic_version` | `campaign_accounts_001` |
| `RUBIKA_CANONICAL_SESSION_V1` in compose/override | unset → app default **false** |

`L4_REPORT_CORRECTED=True` — this section supersedes the earlier wrong Account12=`600,657` / Account79=`728` transcription. Latest L4 summary mapping (`12→657,728` / `79→600`) was already correct.

Artifact: `reports/rubika-remediation/L4_SESSION_ACCOUNT_MAPPING_RECONCILIATION.json`

---

## L3 enum fix (minimal)

**Strategy A:** single CREATE TYPE via `postgresql.ENUM(...).create(checkfirst=True)`; table column uses `create_type=False`.

| Audit | Value |
|---|---|
| `L3_ENUM_CREATE_PATH_COUNT` | 1 |
| `L3_DOUBLE_CREATE_REMOVED` | True |
| `L3_ENUM_LABELS_UNCHANGED` | True |
| `L3_TABLE_SCHEMA_UNCHANGED` | True |
| `L3_DOWNGRADE_ENUM_DROP_SAFE` | True (table first, then enum `checkfirst`) |
| `L2_MIGRATION_UNCHANGED` | True |
| `APPLICATION_LOGIN_CODE_UNCHANGED` | True |

---

## Backup (read-only production dump)

- **Path:** `reports/rubika-remediation/l4_rehearsal/mmp_db_pre_l4_20260831T065954Z.dump`
- **SHA256:** `9d102681dd83c5cffca5a16998cea973d04715f406bf6b8db07d0ff14969374c`
- **Artifact:** `reports/rubika-remediation/L4_MIGRATION_REHEARSAL.json`

---

## Rehearsal results

| Check | Result |
|---|---|
| L2 upgrade on `mmp_l4_rehearsal` | PASS |
| L3 upgrade on `mmp_l4_rehearsal` | PASS |
| L3→L2→base downgrade on `mmp_l4_rehearsal_downgrade` | PASS |
| Rubika accounts | 43 |
| Sessions after L2/L3 | 15 all `legacy_unclassified` |
| ACTIVE created by migration | **0** |
| Account12 sessions | `657`, `728` preserved |
| Account79 sessions | `600` preserved |
| Duplicates | `2,12,19,81,92` unchanged |
| Canonical loader (flag=false) | `NO_ACTIVE_SESSION` for 12/79/1/2 |
| Runner | `mmp_l4_alembic_runner` (not `mmp_core_api`) |

### Feature-flag compatibility

- **Flag=false (rehearsal runner):** schema present; canonical loader returns `NO_ACTIVE_SESSION` for legacy-only rows; no auto-ACTIVE. Legacy runtime remains default in production (flag still false).
- **Flag=true fence:** application login fence code unchanged; proven in isolated L3 unit tests. Production flag remains false.

---

## Real candidate prover (prior L4 work)

- `RealRubikaCandidateProver` implemented; PassThrough blocked outside tests
- Isolated prover tests: 18 passed (stub network; no live Rubika)

---

## Production safety

| Guard | Result |
|---|---|
| Production schema mutation | False |
| Production alembic | unchanged |
| OTP / Rubika network | False |
| Redis / workers | False |
| Flag enabled in production | False |

---

## Next safe action

Prepare production L2/L3 schema apply runbook only after this mapping reconciliation (**complete**). Do **not** enable `RUBIKA_CANONICAL_SESSION_V1` on apply. Schema apply alone must not change runtime behavior.
