# L5 — PRODUCTION L2/L3 SCHEMA APPLY RUNBOOK

**Phase:** L5 (runbook only — **no execution in this document**)  
**Date:** 2026-08-31  
**Target database:** `mmp_db` on `mmp_postgres`  
**Current production revision:** `campaign_accounts_001`  
**Target revisions:** `rubika_l2_canonical_session_001` → `rubika_l3_login_challenge_001`

**Machine-readable plan:** `L5_PRODUCTION_SCHEMA_APPLY_PLAN.json`  
**Authorized wrapper (not auto-run):** `scripts/_l5_production_schema_apply.py`

---

## Scope contract

Schema apply **only**. Must **NOT**:

- create any `ACTIVE` canonical session
- choose a canonical session for duplicate accounts
- enable `RUBIKA_CANONICAL_SESSION_V1`
- request OTP or call Rubika network
- change `RUBIKA_ACCOUNT_IDS=12,79`
- restart workers unless post-check requires it
- enqueue/send messages

After apply, all existing sessions remain `legacy_unclassified`. Runtime stays legacy until a later operator phase.

---

## Authoritative production baseline (reconciled 2026-08-31)

| Item | Value |
|---|---|
| Rubika accounts | 43 |
| Account12 sessions | **657, 728** |
| Account79 sessions | **600** |
| Session 600 → account | 79 |
| Session 657 → account | 12 |
| Session 728 → account | 12 |
| Account12 real-send | True (attempts 2, 15) |
| Account79 real-send | True (attempt 17) |
| `session_status` column | absent |
| Campaign 101 | paused (`R10-CONTROLLED-REAL-SEND-2MSG`) |
| Campaign 102 | paused (`R10-ACCOUNT79-REPLACEMENT-1MSG`) |

---

## Revision chain proof

Linear chain from production current head:

```
campaign_accounts_001
  → rubika_l2_canonical_session_001
    → rubika_l3_login_challenge_001
```

No other migration has `down_revision = campaign_accounts_001`.  
**Do not use ambiguous `alembic upgrade head` on first production apply** — use explicit revision IDs.

Migration source hashes (record before apply):

| File | SHA256 |
|---|---|
| `alembic/versions/rubika_l2_canonical_session_001.py` | `0D1B82FA765CDEECCEA484F2C8FF5F53AB3D0201F8C4674B27F43069E27FAEC6` |
| `alembic/versions/rubika_l3_login_challenge_001.py` | `AED15577C4717C42B2A00D3BC6C86920760494D84A53C3B3A80FA762B9373587` |

L4 rehearsal evidence: L2/L3/downgrade all PASS; `CANONICAL_ACTIVE_SESSIONS_CREATED_BY_MIGRATION=0`.

---

## P1 — Fresh pre-migration backup (mandatory)

**The L4 rehearsal backup is evidence only. Do not use it as the production rollback source.**

### Directory

```powershell
$TS = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$BACKUP_DIR = "C:\Users\poorchista\senderplatformtest\multi-messaging-platform\reports\rubika-remediation\l5_production_apply\backups\$TS"
New-Item -ItemType Directory -Force -Path $BACKUP_DIR
$BACKUP_FILE = Join-Path $BACKUP_DIR "mmp_db_pre_l5_$TS.dump"
```

### pg_dump (read-only on production)

```powershell
docker exec mmp_postgres pg_dump -U mmp_user -Fc -f "/tmp/mmp_db_pre_l5_$TS.dump" mmp_db
docker cp "mmp_postgres:/tmp/mmp_db_pre_l5_$TS.dump" $BACKUP_FILE
```

### Record hash and size

```powershell
$hash = (Get-FileHash $BACKUP_FILE -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $BACKUP_FILE).Length
@{
  BACKUP_PATH = $BACKUP_FILE
  BACKUP_SHA256 = $hash
  BACKUP_SIZE = $size
  BACKUP_TIMESTAMP = $TS
} | ConvertTo-Json | Set-Content (Join-Path $BACKUP_DIR "backup.meta.json")
Write-Host "BACKUP_PATH=$BACKUP_FILE"
Write-Host "BACKUP_SHA256=$hash"
Write-Host "BACKUP_SIZE=$size"
```

**Gate:** `FRESH_BACKUP_CREATED=True` and `FRESH_BACKUP_HASH_VERIFIED=True` before any Alembic.

---

## P2 — Read-only prechecks (ABORT if any fail)

Run **immediately before** migration on `mmp_db`:

```powershell
# Alembic
docker exec mmp_postgres psql -U mmp_user -d mmp_db -tAc "SELECT version_num FROM alembic_version;"
# Expect: campaign_accounts_001

# Fleet count
docker exec mmp_postgres psql -U mmp_user -d mmp_db -tAc "SELECT COUNT(*) FROM accounts WHERE platform::text IN ('rubika','RUBIKA');"
# Expect: 43

# Account12/79 session mapping
docker exec mmp_postgres psql -U mmp_user -d mmp_db -c "SELECT account_id, id AS session_id, session_type::text, created_at FROM channel_sessions WHERE account_id IN (12,79) AND session_type::text IN ('rubika_session','RUBIKA_SESSION') ORDER BY account_id, id;"
# Expect: 12→657,728  79→600

# Independent ownership
docker exec mmp_postgres psql -U mmp_user -d mmp_db -c "SELECT id, account_id FROM channel_sessions WHERE id IN (600,657,728) ORDER BY id;"

# session_status must NOT exist yet
docker exec mmp_postgres psql -U mmp_user -d mmp_db -tAc "SELECT COUNT(*) FROM information_schema.columns WHERE table_name='channel_sessions' AND column_name='session_status';"
# Expect: 0

# Campaigns paused
docker exec mmp_postgres psql -U mmp_user -d mmp_db -c "SELECT id, name, status::text FROM campaigns WHERE id IN (101,102);"
# Expect: both paused

# No active remediation campaign (non-paused R10/test)
docker exec mmp_postgres psql -U mmp_user -d mmp_db -c "SELECT id, name, status::text FROM campaigns WHERE status::text NOT IN ('paused','completed','cancelled') AND name ILIKE '%R10%';"
# Expect: 0 rows

# Real-send evidence preserved
docker exec mmp_postgres psql -U mmp_user -d mmp_db -c "SELECT m.account_id, COUNT(*) FROM messages m JOIN message_attempts ma ON ma.message_id=m.id WHERE ma.status::text='SUCCESS' AND m.account_id IN (12,79) GROUP BY m.account_id;"
# Expect: 12→2, 79→1

# Config flags (compose + .env — no change planned)
docker inspect mmp_core_api --format "{{range .Config.Env}}{{println .}}{{end}}" | findstr RUBIKA_CANONICAL
docker inspect mmp_rubika_worker --format "{{range .Config.Env}}{{println .}}{{end}}" | findstr RUBIKA_ACCOUNT_IDS
# Expect: RUBIKA_CANONICAL unset/false; RUBIKA_ACCOUNT_IDS=12,79

# Queues must be 0 before any restart (use redis password from .env)
# docker exec mmp_redis redis-cli -a "$REDIS_PASSWORD" LLEN queue:rubika:12
# docker exec mmp_redis redis-cli -a "$REDIS_PASSWORD" LLEN queue:rubika:79
# Expect: 0, 0
```

**ABORT** if any value differs from expected.

Wrapper precheck:

```powershell
$env:ALLOW_PRODUCTION_SCHEMA_MIGRATION="1"
$env:FRESH_BACKUP_PATH="<absolute path from P1>"
$env:FRESH_BACKUP_SHA256="<sha256 from P1>"
$env:L5_APPLY_STEP="precheck"
python "C:\Users\poorchista\senderplatformtest\multi-messaging-platform\scripts\_l5_production_schema_apply.py"
```

---

## P3 — Migration runner (NOT mmp_core_api)

Use one-shot container **`mmp_l5_production_alembic_runner`** (same pattern as L4):

| Requirement | Value |
|---|---|
| Image | `multi-messaging-platform-core_api` (or `L5_ALEMBIC_RUNNER_IMAGE`) |
| Network | `multi-messaging-platform_default` |
| Mount | `{PROJECT_ROOT}:/app:ro` |
| `DATABASE_URL` | `postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db` |
| Redis | unset / empty |
| Flags during migrate | `RUBIKA_CANONICAL_SESSION_V1=false` |

**Positive authorization (mandatory):**

```powershell
$env:ALLOW_PRODUCTION_SCHEMA_MIGRATION="1"
$env:FRESH_BACKUP_PATH="..."
$env:FRESH_BACKUP_SHA256="..."
```

Without all three → wrapper hard-refuses `mmp_db`.

Manual equivalent (operator may use wrapper instead):

```powershell
$PROJECT = "C:\Users\poorchista\senderplatformtest\multi-messaging-platform"
$IMAGE = docker inspect -f "{{.Config.Image}}" mmp_core_api
docker run --rm --name mmp_l5_production_alembic_runner `
  --network multi-messaging-platform_default `
  -v "${PROJECT}:/app:ro" `
  -e "DATABASE_URL=postgresql://mmp_user:mmp_pass@postgres:5432/mmp_db" `
  -e "REDIS_URL=" `
  -e "PYTHONDONTWRITEBYTECODE=1" `
  -e "RUBIKA_CANONICAL_SESSION_V1=false" `
  -w /app $IMAGE alembic upgrade rubika_l2_canonical_session_001
```

Print and verify target DB name is **`mmp_db`** before executing.

---

## P4 — Upgrade sequence (explicit revisions)

### Step 1 — Apply L2

```powershell
$env:L5_APPLY_STEP="l2"
python "C:\Users\poorchista\senderplatformtest\multi-messaging-platform\scripts\_l5_production_schema_apply.py"
```

Equivalent: `alembic upgrade rubika_l2_canonical_session_001`

### STOP — P5 L2 verification before L3

```powershell
$env:L5_APPLY_STEP="verify_l2"
python "...\scripts\_l5_production_schema_apply.py"
```

Manual checks:

```sql
SELECT version_num FROM alembic_version;  -- rubika_l2_canonical_session_001
SELECT session_status, COUNT(*) FROM channel_sessions GROUP BY 1;
-- all legacy_unclassified; active count 0
SELECT COUNT(*) FROM channel_sessions;  -- unchanged (15 rubika / total per precheck)
SELECT id FROM channel_sessions WHERE account_id=12 ORDER BY id;  -- 657,728
SELECT id FROM channel_sessions WHERE account_id=79 ORDER BY id;  -- 600
SELECT account_id, COUNT(*) FROM channel_sessions
  WHERE session_type::text IN ('rubika_session','RUBIKA_SESSION')
  GROUP BY 1 HAVING COUNT(*)>1;  -- duplicates preserved
```

**If any L2 verify fails → DO NOT run L3.** Rollback per P10/P11.

### Step 2 — Apply L3 (only after L2 PASS)

```powershell
$env:L5_APPLY_STEP="l3"
python "...\scripts\_l5_production_schema_apply.py"
```

Equivalent: `alembic upgrade rubika_l3_login_challenge_001`

---

## P6 — Post-L3 verification

```powershell
$env:L5_APPLY_STEP="verify_post"
python "...\scripts\_l5_production_schema_apply.py"
```

Manual checks:

```sql
SELECT version_num FROM alembic_version;  -- rubika_l3_login_challenge_001
SELECT to_regclass('rubika_login_challenges');  -- exists
SELECT COUNT(*) FROM rubika_login_challenges;  -- 0
SELECT COUNT(*) FROM pg_type WHERE typname='rubikaloginchallengestate';  -- 1
SELECT session_status, COUNT(*) FROM channel_sessions GROUP BY 1;
-- still all legacy_unclassified; active 0
```

No OTP columns/values. No account row changes beyond new nullable identity columns defaults.

---

## P7 — Feature flags (unchanged after schema apply)

| Setting | Required value |
|---|---|
| `RUBIKA_CANONICAL_SESSION_V1` | **false** (do not enable) |
| `AUTO_ENROLL_RUBIKA_POOL` | **false** |
| `RUBIKA_ACCOUNT_IDS` | **12,79** (keep pin) |

Schema-only apply must not change dispatch behavior. Legacy max(id) runtime remains default.

---

## P8 — Application restart requirements

### Mount topology audit

| Service | Code mounts | Alembic in image | Restart for schema-only? |
|---|---|---|---|
| `mmp_core_api` | `./core_engine`, `./workers`, `./scripts` | baked + mounts | **Recommended** — refresh pool / ORM |
| `mmp_celery_worker` | `./core_engine`, `./workers` | mounts | **Optional** — only if tasks fail |
| `mmp_rubika_worker` | **none** (image only) | baked | **Defer** — legacy path ignores new columns while flag=false |
| `mmp_celery_beat` | none | baked | **Not required** |

### Minimal restart plan

1. Confirm `queue:rubika:12` and `queue:rubika:79` length **0**
2. Restart API only:

```powershell
docker compose restart core_api
```

3. **Do not** restart `mmp_rubika_worker` unless post-check shows readiness regression
4. Wait for health; verify no send operations triggered

### Post-restart read-only checks (no send)

```powershell
docker ps --filter name=mmp_core_api --filter name=mmp_rubika_worker
# API health endpoint if available
# Worker coverage keys (redis): worker:coverage:rubika:12 / :79 should repopulate via heartbeat
# Re-run fleet health audit script read-only if needed
```

---

## P9 — Post-migration read-only verification

Same as P6 plus:

- TOTAL_RUBIKA_ACCOUNTS=43
- Account12 sessions 657,728; Account79 session 600
- All sessions `legacy_unclassified`; canonical ACTIVE=0
- Login challenges=0
- Campaigns 101/102 still paused
- No new messages/attempts since backup timestamp
- Real-send historical counts unchanged (12→2, 79→1)

---

## P10 — Rollback triggers

| Trigger | Action |
|---|---|
| L2 migration error before completion | **D** full restore OR **C** if partial — prefer restore |
| L2 verify: any ACTIVE row | **C** downgrade L2; if fails **D** |
| L2 verify: session count/id mismatch | **D** full restore |
| L2 verify: Account12/79 session loss | **D** full restore |
| L3 migration error | **B** downgrade L3 only (if L2 good) |
| L3 verify: challenges ≠ 0 | **B** then investigate; **D** if corrupt |
| Post-restart API unhealthy | **D** if schema rollback insufficient |
| Unexpected Account12/79 readiness loss | Stop; **D** if not resolved by API restart only |
| Any production send/OTP triggered | **D** + incident review |

Legend: **A** stop before L3 · **B** downgrade L3 · **C** downgrade L3+L2 · **D** pg_restore

---

## P11 — Exact rollback commands (manual — require authorization)

Set authorization env vars first. Use isolated runner `mmp_l5_production_alembic_runner`.

### B — Downgrade L3 only (L2 remains)

```powershell
$env:ALLOW_PRODUCTION_SCHEMA_MIGRATION="1"
$env:FRESH_BACKUP_PATH="..."
$env:FRESH_BACKUP_SHA256="..."
$env:L5_APPLY_STEP="downgrade_l3"
python "...\scripts\_l5_production_schema_apply.py"
```

Manual: `alembic downgrade rubika_l2_canonical_session_001`

### C — Downgrade L3 + L2

```powershell
$env:L5_APPLY_STEP="downgrade_l3"
python "...\scripts\_l5_production_schema_apply.py"
$env:L5_APPLY_STEP="downgrade_l2"
python "...\scripts\_l5_production_schema_apply.py"
```

Manual sequence:

```
alembic downgrade rubika_l2_canonical_session_001
alembic downgrade campaign_accounts_001
```

Verify: `session_status` column gone; `rubika_login_challenges` gone; `alembic_version=campaign_accounts_001`.

### D — Full restore from fresh backup

```powershell
# DESTRUCTIVE — requires operator approval + maintenance window
docker exec mmp_postgres psql -U mmp_user -d postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='mmp_db' AND pid <> pg_backend_pid();"
docker exec mmp_postgres psql -U mmp_user -d postgres -c 'DROP DATABASE IF EXISTS "mmp_db";'
docker exec mmp_postgres psql -U mmp_user -d postgres -c 'CREATE DATABASE "mmp_db" OWNER mmp_user;'
docker cp $BACKUP_FILE mmp_postgres:/tmp/restore.dump
docker exec mmp_postgres pg_restore -U mmp_user -d mmp_db --no-owner --no-acl /tmp/restore.dump
docker exec mmp_postgres psql -U mmp_user -d mmp_db -tAc "SELECT version_num FROM alembic_version;"
```

Re-verify session mapping 12→657,728; 79→600 after restore.

---

## P12 — Production apply gates (all required before execution)

| Gate | Required |
|---|---|
| `FRESH_BACKUP_CREATED` | True |
| `FRESH_BACKUP_HASH_VERIFIED` | True |
| `PRECHECKS_PASS` | True |
| `MIGRATION_SOURCE_HASHES_RECORDED` | True |
| `L2_REHEARSAL_PASS` | True |
| `L3_REHEARSAL_PASS` | True |
| `DOWNGRADE_REHEARSAL_PASS` | True |
| `ACCOUNT12_MAPPING_RECONCILED` | True |
| `ACCOUNT79_MAPPING_RECONCILED` | True |
| `RUBIKA_CANONICAL_SESSION_V1` | False |
| `OPERATOR_APPROVAL_REQUIRED` | True (explicit sign-off) |

---

## Execution checklist (operator)

- [ ] P1 fresh backup + hash recorded
- [ ] P2 prechecks PASS
- [ ] P4 L2 apply
- [ ] P5 L2 verify PASS — **STOP if fail**
- [ ] P4 L3 apply
- [ ] P6 post-L3 verify PASS
- [ ] P7 flags confirmed unchanged
- [ ] P8 `core_api` restart only; queues 0; worker restart deferred
- [ ] P9 post-migration verification PASS
- [ ] Record execution artifact

**This runbook does not execute any step automatically.**
