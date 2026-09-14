# R10 Created-Data Forensic Inventory

**Phase:** `R10_FORENSIC_DATA_INVENTORY` → cleanup plan ready, **NOT executed**  
**Captured:** 2026-08-29  
**Mode:** READ-ONLY attribution (no delete / send / OTP / worker start)  
**Git HEAD:** `72682ca` on `remediation/rubika-multi-account-production-20260826_160629`  
**Worker:** `mmp_rubika_worker` **Exited** · running campaigns = **0** · `R10_SEND_EXECUTED=False`

## Anchors

| Item | Value |
|------|-------|
| R0 dump | `C:\Users\poorchista\senderplatform-r0-backups\20260826_161259\mmp_postgres_r0_3.dump` |
| R0 SHA256 | `4f41279a7bf5c2b54e91e8fd36f920185219d5c32aa825b14bf9cd76864f2c05` |
| Diff source | `R10_R0_DIFF.json` + live `psql` refresh |
| Authorized R10 accounts | **12, 79** only |
| Operator-approved contacts | **2, 92** (92 = KEEP even if post-R0) |

Machine JSON: `reports/rubika-remediation/R10_CREATED_DATA_FORENSIC_INVENTORY.json`

---

## Live vs R0 baseline

| Table | R0 count | Live now | New IDs (live − R0) |
|-------|----------|----------|---------------------|
| accounts | 48 | 158 | **110** (`2095`–`2204`) |
| contacts | 2 | 337 | **335** (`3`–`338`) |
| campaigns | 4 | 99 | **95** (`5`–`100` except 62) |
| campaign_accounts | 5 | 100 | **95** |
| campaign_recipients | 4 | 339 | **335** |
| messages | 1 | 336 | **335** |
| staged_queue_items | 1 | 336 | **335** |
| message_attempts | 2 | 14 | **12** (`3`–`14`) |
| channel_sessions | 6 | 120 | **114** (105 on test accts; 9 on prod accts KEEP) |
| rubika_account_pool | 44 | 99 | **55** |
| rubika_sender_schedules | 1 | 21 | **20** (all `p6-*`, inactive) |

No R0 primary keys were deleted (`ids_in_r0_not_live` empty for compared tables).

---

## 2) Accounts — critical

```
ACCOUNT_COUNT_R0_BASELINE=48
ACCOUNT_COUNT_NOW=158
NEW_ACCOUNT_IDS=2095..2204 (110 ids)
NEW_RUBIKA_ACCOUNT_IDS=2095..2204 (110 ids)
NEW_ACCOUNTS_CREATED_BY_REMEDIATION=True
```

- All 110 classified **pytest debris** (labels `r22-*`, `healthy`/`sick`/`auto-a`/…); **unknown=0**.
- Batches: 2026-08-26 (~66) and 2026-08-29 (~44) matching R2 / R3–R9 pytest windows.
- Accounts **12** and **79** pre-existed in R0 — **not** created by remediation.
- **Do not delete accounts without operator approval** (plan lists them as DELETE_CANDIDATE only).

---

## 3) Contacts

```
NEW_CONTACT_IDS=3..338 (335 ids; see JSON)
NEW_CONTACTS_CREATED_BY_REMEDIATION=True
```

| contact_id | In R0? | Role |
|------------|--------|------|
| 1 | yes | Owner recipient on campaigns 1 & 2 |
| 2 | yes | Operator-approved R10 Recipient A |
| **92** | **no** (created 2026-08-26 during p6 window) | Operator-approved R10 Recipient B — **KEEP** |

**Account id=92 ≠ Contact id=92** (different tables/FKs).

---

## 4) Campaigns

### KEEP (R0 / production)

| id | name | status | class | accounts | recipients | messages |
|----|------|--------|-------|----------|------------|----------|
| 1 | rubika test1 | draft | owner UI (audit admin, in R0) | 92 | 1 (contact **1**) | 0 |
| 2 | rubikaa test 2 | draft | owner UI (audit admin, in R0) | 79 | 1 (contact **1**) | 0 |
| 3 | ACCOUNT12-PILOT-ONE-MESSAGE | paused | production pilot | 12 | 1 | 1 |
| 4 | 1 | draft | production | 12,79 | 1 | 0 |

### NEW during remediation (test debris)

```
NEW_CAMPAIGN_IDS=5..61,63..100 (95 ids)
NEW_CAMPAIGNS_CREATED_BY_REMEDIATION=True
```

- **55** `r22-*` + **40** `p6-*`
- Timestamps cluster at 2026-08-26 13:38Z and 2026-08-29 07:39Z
- **No** new campaign uses accounts 12 or 79
- **No** dedicated R10 campaign exists yet (`r10_dedicated_campaign_exists=False`)

---

## 5) Messages / staged / attempts

```
NEW_MESSAGES_CREATED_BY_REMEDIATION=True
NEW_MESSAGE_IDS=2..336 (335 ids)
```

- KEEP message **1** (campaign 3 pilot).
- Attempts **3–14**: pytest stubs (`platform_message_id='x'`).
- KEEP attempt **2** (real pilot SUCCESS).

---

## 6) Schedules / pool / sessions

- **KEEP** schedule **195** (`day`, active, 8–22, max=50) — matches R0 intent; R2.2 restored active state.
- **DELETE_CANDIDATE** schedules: `256,257,258,259,260,261,262,263,265,266,278,279,280,281,282,283,284,285,287,288` (all inactive `p6-*`).
- **55** new pool rows — none attach to accounts 12/79.
- Sessions **600** (acct79) / **657** (acct12): ciphertext sha16 **matches R0**.
- Post-R0 sessions on production accounts (**721–729** etc.): **KEEP** (no session delete without separate approval).
- Sessions on accounts 2095–2204: **DELETE_CANDIDATE** (105 ids listed in JSON).

---

## 7) Attribution (not name-only)

| Evidence | Conclusion |
|----------|------------|
| Absent from R0 dump SQL extracts | Created after 2026-08-26 16:12 local |
| Labels / campaign names `r22-*` `p6-*` + fixture labels | Match parity/phase6 test factories |
| Timestamps align with commits `56b84bb`…`72682ca` pytest runs | Remediation test pollution of live DB |
| Owner campaigns 1–2 present in R0 + audit `create_campaign` / admin | Pre-remediation production UI |
| No R10 campaign / queues empty / worker exited | R10 did not send |

---

## 8) Cleanup plan — **DO NOT EXECUTE**

### KEEP (summary)

| table | id | reason |
|-------|-----|--------|
| accounts | all ≤92 (esp. 12, 79, 92) | production / authorized |
| campaigns | 1,2,3,4 | R0 / owner UI / pilot |
| contacts | 1, 2, **92** | R0 + operator KEEP |
| rubika_sender_schedules | 195 | production day |
| channel_sessions | 600,657,11,12 + 721–729 (prod) | production; no blind delete |
| messages / attempts | msg 1; attempts ≤2 | real pilot history |
| pool | account_id≤92, non-p6 phases | production |

### DELETE (exact IDs — children → parents)

1. `message_attempts` **3–14**
2. `staged_queue_items` **2–336** (335 ids; full list in JSON)
3. `messages` **2–336**
4. `campaign_recipients` new set (335 ids; JSON)
5. `campaign_accounts` **7–101**
6. `campaigns` **5–61,63–100**
7. `rubika_sender_schedules` **256…288** (20 ids)
8. `rubika_account_pool` **225…320** new set (55 ids; JSON)
9. `channel_sessions` test-account set (105 ids; JSON) — **not** 721–729
10. `contacts` new except **92** (334 ids)
11. `accounts` **2095–2204**

Also purge orphan `rendered_messages` / registry rows FK-tied only to deleted campaigns (enumerate in cleanup script before commit).

### RESTORE

| table | id | current | expected | action |
|-------|-----|---------|----------|--------|
| rubika_sender_schedules | 195 | day active 8–22 max=50 | same (R0) | **none** |
| channel_sessions | 600,657 | sha16 = R0 | unchanged | **none** |

**No wildcard / TRUNCATE / broad reset. No execution until operator approval.**

---

## 9) Required gates

```
NEW_ACCOUNTS_CREATED_BY_REMEDIATION=True
NEW_ACCOUNT_IDS=2095,2096,...,2204

NEW_CONTACTS_CREATED_BY_REMEDIATION=True
NEW_CONTACT_IDS=3,4,...,338

NEW_CAMPAIGNS_CREATED_BY_REMEDIATION=True
NEW_CAMPAIGN_IDS=5..61,63..100

NEW_MESSAGES_CREATED_BY_REMEDIATION=True
NEW_MESSAGE_IDS=2..336

TEST_DEBRIS_CONFIRMED=True
TEST_DEBRIS_RECORD_COUNT=1831

UNAUTHORIZED_PRODUCTION_DATA_CREATED=True

R10_SEND_EXECUTED=False
MESSAGE_SENT=False
OTP_REQUESTED=False

CURRENT_PHASE=R10_FORENSIC_DATA_CLEANUP
PHASE_STATUS=BLOCKED

BLOCK_REASON=Exact cleanup plan requires operator review
EXACT_OPERATOR_INPUT_REQUIRED=Approve/reject exact-ID cleanup plan
SAFE_STATE_CONFIRMED=True
```

**Stopped.** No real send until cleanup plan is approved or explicitly deferred by operator.
