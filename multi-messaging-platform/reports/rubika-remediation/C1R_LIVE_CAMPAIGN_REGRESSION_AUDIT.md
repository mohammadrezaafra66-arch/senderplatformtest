# C1R — Live Campaign Sender Picker Regression Audit

Generated: 2026-09-01 (in progress)

## Root cause (proven)

**C1R_ROOT_CAUSE=STALE_FRONTEND_BUNDLE + OLD_COMPONENT_STILL_RENDERED + DISPLAY_COMPONENT_FALLBACK_TO_ENABLED**

The committed/deployed `CampaignSenderSelector` (git HEAD) rendered:

```tsx
<small>{account.platform} · {t(`account_status_${account.status}`)}</small>
```

For lifecycle-`active` Rubika accounts this produces **`rubika · فعال`** (RTL may display as **`فعال · rubika`**).

This uses **Account.status** (lifecycle enabled), NOT L18 `runtime_status`. Prior C1 source changes existed only in the **working tree** and were **not fully deployed** to the running frontend image.

The campaign readiness **summary/preflight table** uses separate paths with READY/coverage — hence **summary vs picker mismatch**.

## Component trace

| Item | Value |
|------|-------|
| LIVE_SENDER_COMPONENT_PATH | `frontend/src/components/CampaignSenderSelector.tsx` |
| GENERIC_ACTIVE_LABEL_SOURCE | `t('account_status_${account.status}')` in old bundle line `{account.platform} · ...}` |
| OLD_COMPONENT_STILL_RENDERED | **True** (live container chunk `0xqumox_k3l04.js` before redeploy) |
| DUPLICATE_SENDER_PICKER_IMPLEMENTATIONS | **1** component; separate assigned-sender summary in `campaigns/[id].tsx` |

## API contract

| Item | Value |
|------|-------|
| LIVE_SENDER_PICKER_ENDPOINT | `GET /accounts` via `fetchAccounts()` |
| LIVE_RESPONSE_HAS_RUNTIME_STATUS | **True** (backend L18 fields present) |
| LIVE_RESPONSE_HAS_CAMPAIGN_ELIGIBLE | **True** |
| LIVE_RESPONSE_HAS_BLOCKER | **True** when non-eligible |

Backend was not the primary failure layer; frontend ignored runtime fields in the deployed picker.

## Fix applied (source)

1. `resolveCampaignSenderStatusLabel()` — shared operational label (never lifecycle `active` alone)
2. `CampaignSenderStatusLine` — shared renderer
3. `CampaignSenderSelector` — uses shared renderer + eligibility-gated checkboxes
4. `campaigns/[id].tsx` assigned-sender summary — removed `account_status_*` fallback
5. Vitest regression: `frontend/src/utils/sender-accounts.test.ts` (**SCREENSHOT_REGRESSION_TEST_PASS=True**)

## Deploy status

Frontend Docker rebuild **attempted** but **blocked**: Docker Desktop filesystem read-only / daemon crash during `docker compose up frontend`.

**LIVE_BROWSER_POSTDEPLOY_VERIFY=False** until frontend redeploy succeeds.

## M4

**Not started** — blocked on `FINAL_CAMPAIGN_LIVE_UI_TRUTH_PASS=False` (deploy gate).
