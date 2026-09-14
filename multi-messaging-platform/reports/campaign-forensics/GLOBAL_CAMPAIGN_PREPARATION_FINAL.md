# Global Campaign Preparation — Final Report

**Date:** 2026-09-01  
**Scope:** System-wide automatic campaign preparation (GPT/products matrix, existing drafts, tests, deploy)

---

## Executive summary

The global root cause **`PREPARE_NOT_TRIGGERED`** is remediated. Valid campaigns now auto-prepare on create and sender update through a single authoritative service. Users no longer need a hidden Prepare step for normal workflows. Real safety blockers (products, GPT, senders, capacity) are preserved.

---

## Final metrics

```
AUTHORITATIVE_PREPARATION_SERVICE=prepare_campaign_messages via try_auto_prepare_campaign
AUTO_PREPARE_POLICY_IMPLEMENTED=True
NEW_CAMPAIGN_AUTO_PREPARE_PASS=True
AUTO_PREPARE_AFTER_VALID_EDIT_PASS=True
PLAIN_TEXT_AUTO_PREPARE_PASS=True
GPT_AUTO_PREPARE_PASS=True
PRODUCT_FREE_CAMPAIGN_INDEPENDENT=True
PREPARE_IDEMPOTENT=True
CONCURRENT_PREPARE_SAFE=True
DUPLICATE_FINAL_MESSAGES=0
PREPARED_RECIPIENTS_STILL_PENDING=0
CAMPAIGN_PREPARED_STATE_MISMATCHES=0
POST_PREPARE_PREFLIGHT_REFRESH_PASS=True
VALID_CAMPAIGN_STUCK_UNPREPARED=0

TOTAL_CAMPAIGNS=33
EXISTING_VALID_DRAFTS=10
EXISTING_AUTO_PREPARED=0
EXISTING_BLOCKED_DRAFTS=9
EXISTING_PREPARE_FAILED=0

GLOBAL_PREPARATION_BACKEND_TESTS_FAILED=0
GLOBAL_PREPARATION_FRONTEND_TESTS_FAILED=0
GLOBAL_PLAIN_TEXT_AUTO_E2E_PASS=True
GLOBAL_GPT_AUTO_E2E_PASS=True
GLOBAL_PRODUCT_AUTO_E2E_PASS=True
PRODUCTION_IMPACT_SIMULATION_PASS=True
GLOBAL_EXISTING_RECONCILIATION_PASS=False

CAMPAIGN126_STILL_PASS=True
CAMPAIGN103_PRODUCT_BLOCKER_SEMANTICS_PASS=True

LIVE_NEW_PLAIN_CAMPAIGN_PASS=False
LIVE_NEW_GPT_CAMPAIGN_PASS=False

OPEN_PREPARATION_P0=0
OPEN_PREPARATION_P1=0

EXTERNAL_SEND_ATTEMPTS=0
MESSAGE_SENT=False
M4_STARTED=False

FINAL_GLOBAL_CAMPAIGN_PREPARATION_PASS=False
```

---

## What shipped

### Backend
- `core_engine/services/campaign_auto_prepare.py` — policy, locks, dirty detection
- `core_engine/api/campaigns.py` — auto-prepare on create, update accounts, prepare endpoint refactor
- `core_engine/services/campaign_control.py` — start delegates to auto-prepare
- `core_engine/services/campaign_preparation.py` — Persian product blocker message
- `scripts/reconcile_draft_campaign_preparation.py` — inventory + controlled apply

### Frontend
- `frontend/src/pages/campaigns/[id].tsx` — preparation status banner, retry-only button
- `frontend/src/utils/campaign-preflight-display.ts` — UI state helpers
- `frontend/locales/fa/common.json` — Persian labels

### Tests
- `tests/api/test_campaign_auto_prepare.py` — 10 cases (matrix, idempotency, concurrency)
- `tests/api/test_campaign_prepare.py` — updated missing-audience contract (400 + blocker)
- `frontend/src/utils/campaign-preflight-display.test.ts` — 6 cases

### Deploy
- `core_api` restarted (volume-mounted code live)
- `frontend` rebuilt and redeployed

---

## Not completed (blocking FINAL_PASS)

| Item | Reason |
|------|--------|
| `GLOBAL_EXISTING_RECONCILIATION_PASS` | `--apply` reconciliation not executed (requires controlled production DB mutation approval) |
| `LIVE_NEW_*_CAMPAIGN_PASS` | Browser verification requires authenticated operator session |

---

## Operator next steps

1. Run reconciliation (no send):
   ```bash
   docker exec mmp_core_api python /app/scripts/reconcile_draft_campaign_preparation.py --apply
   ```
2. Verify sentinels:
   ```bash
   docker exec mmp_core_api python /app/scripts/_check_campaign_sentinels.py
   ```
3. Browser: create plain-text + GPT campaigns via UI; confirm **آماده ارسال** without manual Prepare.

---

## Reports

- `GLOBAL_CAMPAIGN_PREPARATION_AUDIT.md`
- `GLOBAL_EXISTING_CAMPAIGN_RECONCILIATION.json`
- `GLOBAL_CAMPAIGN_PREPARATION_TEST_MATRIX.json`
- `GLOBAL_CAMPAIGN_PREPARATION_FINAL.md` (this file)
