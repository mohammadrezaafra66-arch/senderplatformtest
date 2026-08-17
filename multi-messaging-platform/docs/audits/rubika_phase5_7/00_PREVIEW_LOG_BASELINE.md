# Phase 5.7 — Preview / log baseline (forensic)

START_HEAD: `0dec79fc7c4cb9ed13f7e90d30e6afd591c2cd2d`  
Branch: `feature/rubika-module`

## Classification

| Area | Status | Notes |
| --- | --- | --- |
| Canonical final composition used by prepare | PARTIAL | Prepare inlines GPT assign → placeholder sub → product compose. GPT preview uses a separate path (`build_gpt_preview`) that does not substitute recipient placeholders. |
| Worker uses persisted `final_text` | ALREADY_VERIFIED | `queue_payload.final_text` → `payload_adapter.message_text`. Retry copies JSON and only increments `attempt`. |
| Retry does not call GPT / product API | ALREADY_VERIFIED | Phase 5.5 / 5.6 tests. |
| `RenderedMessage.final_text` persisted | ALREADY_VERIFIED | Also copied to `Message.rendered_text` at prepare. |
| GPT metadata in `queue_payload.metadata` | ALREADY_VERIFIED | `generation_batch_id`, `variation_id`, provider, model, pool. |
| Frozen product snapshot metadata | ALREADY_VERIFIED | Per-message snapshot; worker never refetches. |
| Sender assignment on Message / payload | ALREADY_VERIFIED | `account_id` + UI `MessageSenderCell`. |
| `render_version` | MISSING | Not stored. |
| `render_batch_id` | MISSING | Prepare uses a private `prepare_nonce` only for product RNG. |
| `final_text_sha256` | MISSING | Text stored, no hash. |
| Hash assert before transport | MISSING | Queue bridge does not verify content hash. |
| Sample composition preview endpoint | MISSING | Only GPT suggestion preview (`POST /campaigns/gpt-preview`). |
| Three preview concepts labeled in UI | MISSING | Create page labels GPT samples as preview; no sample-vs-committed distinction. |
| Committed samples on campaign detail | MISSING | `/campaigns/[id]` has progress/senders/recipient table, no `RenderedMessage.final_text` samples. |
| Message log exact text column | MISSING | Columns: #, phone, name, render, send, sender, updated. |
| List excerpt + `has_more` | MISSING | Recipients API returns no text. |
| Message detail / full-text viewer | MISSING | No detail route, no More modal. |
| Long-text collapse UX | MISSING | |
| Frontend-supplied `final_text` as commit | ALREADY_VERIFIED | Create/prepare do not accept client final text. GPT preview `extra=forbid`. |
| Template edit after prepare | PARTIAL | No public template-edit API. Direct DB change + re-prepare **refreshes `ready`** items and **leaves `queued`/`sent` frozen**. UI does not mark stale. |
| Re-prepare transactional all-or-nothing | PARTIAL | GPT/product fetch fails before writes (clean). Mid-loop `HTTPException` is not rolled back inside `prepare_campaign_messages`. |
| RBAC for message logs | ALREADY_VERIFIED | List/export: admin, operator, viewer. Campaign mutate: admin/operator. |
| Secrets in preview/log APIs | ALREADY_VERIFIED | GPT/product status omit keys. |
| XSS-safe log rendering | PARTIAL | React text nodes (safe) but no message text is shown yet. |
| Pagination / campaign / status filters | ALREADY_VERIFIED | Recipients list. |
| N+1 on recipient list | ALREADY_VERIFIED | Constant 3 SELECTs with joinedload of Message.account. |
| Preview→commit reuse | MISSING (intentionally not required) | Safer: sample preview then prepare freezes a new batch. |

## Pipeline today

1. Create campaign from import (template, `use_gpt`, `include_products`, senders).
2. Start → `prepare_campaign_messages` → persist `RenderedMessage` / `Message` / `StagedQueueItem`.
3. Queue bridge pushes `queue_payload` (must contain `final_text`).
4. Worker `normalize_queue_payload` maps `final_text` → `message_text` for transport.
5. Message log UI reads `GET /campaigns/{id}/recipients` (no text).

## Conflicts

None that block Phase 5.7. The only behavioral tension is **ready-item refresh on re-prepare** (existing Phase 4 contract vs “immutable after prepare”). Resolution: keep refresh for unsent `ready` rows as an explicit new `render_batch_id`; never mutate queued/sent history.
