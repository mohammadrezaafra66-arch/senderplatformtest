# Campaign #126 Preparation Root Cause

## Summary

Campaign #126 (`include_products=False`, `use_gpt=False`) **could prepare successfully** via `POST /campaigns/126/prepare`. The operator-visible failure was **not** product feed, GPT, or sender eligibility.

## Root Causes

### 1. `PREPARE_NOT_TRIGGERED` (primary)

- Campaign stayed in `draft` with `campaign_prepared=False` because **Prepare was never invoked**.
- Start was blocked by `CAMPAIGN_NOT_PREPARED` while Start’s backend path would auto-prepare — UI correctly disables Start until prepared, but operator must click **آماده‌سازی کمپین**.
- Preflight for #126 before prepare: `preparation_ready=True`, `preparation_blockers=[]`.

### 2. `CAMPAIGN_STATE_NOT_UPDATED` on recipient (secondary / UI bug)

- After successful prepare, `CampaignRecipient.render_status` remained `pending` even though:
  - `Message` row existed
  - `RenderedMessage.ready_for_queue=True`
  - `StagedQueueItem.status=ready`
  - `campaign.status=prepared`
- UI message log showed `render=pending`, making preparation appear failed.

## Fix (generic)

**File:** `core_engine/services/phase4_prepare.py`

- Set `recipient.render_status = RenderStatus.RENDERED` when a final message is materialized (new or refreshed staged row).

## Campaign #126 Before / After

| Metric | Before Prepare | After Fix + Re-Prepare |
|--------|----------------|------------------------|
| `include_products` | `False` (UI/API/DB match) | same |
| `use_gpt` | `False` | same |
| `campaign_prepared` | `False` | `True` |
| `FINAL_MESSAGE_COUNT` | 0 | 1 |
| `render_status` | `pending` | `rendered` |
| `CAMPAIGN_NOT_PREPARED` | present | **gone** |
| `allowed_to_start` | `False` | `True` |
| `EXTERNAL_SEND_ATTEMPTS` | 0 | 0 |

## Tests Added

- `test_plain_text_golden_prepare_without_products_or_gpt`
- `test_prepare_creates_one_final_message` asserts `RenderStatus.RENDERED`

## Pipeline Stage Audit

| Stage | Pass | Notes |
|-------|------|-------|
| Campaign create | ✓ | draft |
| Audience | ✓ | 1 recipient, valid IR phone |
| Message source | ✓ | template 36 chars |
| Prepare API | ✓ | HTTP 200 |
| Render | ✓ | staged ready |
| Final message persistence | ✓ | message_id=353 |
| Sender assignment | ✓ | account 79 at message level |
| Preflight | ✓ | no blockers after prepare |
| Start gate | ✓ | `allowed_to_start=True` |

**FIRST_BROKEN_STAGE (before operator action):** Prepare trigger (workflow), not render engine.
