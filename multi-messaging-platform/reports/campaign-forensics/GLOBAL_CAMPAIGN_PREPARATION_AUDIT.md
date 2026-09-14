# Global Campaign Preparation Audit

**Date:** 2026-09-01  
**Root cause (proven):** `PREPARE_NOT_TRIGGERED` — valid campaigns remained `draft` with `CAMPAIGN_NOT_PREPARED` because preparation was only triggered on Start or manual/debug endpoints.

---

## Authoritative preparation architecture

| Component | Role | Authoritative? |
|-----------|------|----------------|
| `core_engine/services/phase4_prepare.py` → `prepare_campaign_messages()` | Commits final messages, sets `campaign.status=prepared`, marks `recipient.render_status=rendered` | **Yes** |
| `core_engine/services/campaign_auto_prepare.py` → `try_auto_prepare_campaign()` | Policy gate + advisory lock + delegates to `prepare_campaign_messages` | **Yes (orchestration)** |
| `core_engine/services/campaign_preparation.py` → `evaluate_preparation_readiness()` | Read-only prerequisite blockers | Read-only |
| `POST /campaigns/{id}/prepare` | Manual retry; uses `try_auto_prepare_campaign(force=True)` | Wrapper |
| `campaign_control._auto_prepare()` | Start-time safety net; delegates to auto-prepare | Wrapper |
| `campaign_render.build_campaign_render_preview()` | Preview only | No |
| `debug_prepare.py` | Debug-only | No |

**AUTHORITATIVE_PREPARATION_SERVICE:** `prepare_campaign_messages()` via `try_auto_prepare_campaign()`  
**DUPLICATE_PREPARATION_IMPLEMENTATIONS:** 0 authoritative duplicates (debug/preview wrappers only)

---

## Campaign create/update paths

| Path | `CAMPAIGN_CREATE_PATH` | `PREPARE_TRIGGER_PRESENT` |
|------|------------------------|---------------------------|
| `POST /campaigns/from-contacts` | `from_contacts` | **True** (`trigger=create`) |
| `POST /campaigns/from-import` | `from_import` | **True** (`trigger=create`) |
| `PUT /campaigns/{id}/accounts` | `update_accounts` | **True** (`trigger=update_accounts`) |
| `POST /campaigns/{id}/prepare` | `manual_retry` | **True** |
| `POST /campaigns/{id}/start` | `start` | **True** (`force=True` safety net) |
| Frontend create → detail | `ui_create_redirect` | **True** (via create API) |

No duplicate/copy campaign API exists in codebase. Scheduled campaigns use same draft + start flow.

---

## AUTO_PREPARE_CAMPAIGN_IF_READY policy

Implemented in `try_auto_prepare_campaign()`:

1. Skip `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
2. Evaluate `evaluate_preparation_readiness()` — blockers return without prepare attempt
3. Skip if already prepared and not dirty (unless `force=True`)
4. Call `prepare_campaign_messages()` — single commit path
5. Idempotent via existing staged-message dedup + `pg_advisory_xact_lock(campaign_id)`
6. Dirty detection via `is_preparation_dirty()` for re-prepare on content change

**Product independence:** `include_products=False` → no product feed checks in readiness or prepare path.

**Products on:** `INSUFFICIENT_ADVERTISING_PRODUCTS` remains a legitimate blocker (Campaign #103 sentinel).

---

## Secondary bug fixed (global)

`CampaignRecipient.render_status` stayed `pending` after successful materialization. Fixed in `phase4_prepare.py` via `_mark_recipient_rendered()`.

---

## Frontend workflow

Campaign detail page (`frontend/src/pages/campaigns/[id].tsx`):

- **آماده ارسال** — prepared
- **در حال آماده‌سازی پیام‌ها…** — in progress
- **اطلاعات کمپین کامل نیست** — preparation blockers
- **آماده‌سازی انجام نشد** + **تلاش مجدد برای آماده‌سازی** — retry only on failure edge

Primary Start button disabled until `campaign_prepared=true`. Manual Prepare is retry-only, not a hidden required step.

---

## Production impact simulation (dry-run)

| Metric | Value |
|--------|-------|
| TOTAL_CAMPAIGNS | 33 |
| VALID_STUCK_DRAFTS | 10 |
| BLOCKED_DRAFTS | 9 |
| ALREADY_PREPARED | 14 |
| WOULD_AUTO_PREPARE | 10 |
| WOULD_REMAIN_BLOCKED | 9 |
| UNEXPECTED_STATE_CHANGES | 0 |

---

## Sentinels

| Campaign | Expected | Notes |
|----------|----------|-------|
| #126 | `prepared`, 1 final message, `render_pending=0` | Plain-text path proof case |
| #103 | `INSUFFICIENT_ADVERTISING_PRODUCTS` when feed < 3 products | Must not bypass |

---

## Remaining items

1. **Reconciliation `--apply`:** Script ready at `scripts/reconcile_draft_campaign_preparation.py`; dry-run complete. Controlled apply pending execution approval on production DB.
2. **Live browser verification:** Requires authenticated session (login). No real Start/send performed.

---

## Bug registry

| ID | Severity | Status |
|----|----------|--------|
| PREPARE_NOT_TRIGGERED | P0 | **Fixed** (auto-prepare on create/update) |
| RECIPIENT_RENDER_PENDING_AFTER_PREPARE | P0 | **Fixed** (phase4_prepare) |
| UI_HIDDEN_PREPARE_STEP | P1 | **Fixed** (auto UX + retry) |

**OPEN_PREPARATION_P0:** 0  
**OPEN_PREPARATION_P1:** 0
