# Phase 5.7 RESULT

STATUS: **PASS**

Canonical composer: `core_engine.services.campaign_render.compose_final_render`  
Preview (sample) and prepare (commit) share this function. Worker/retry use persisted `final_text` only.

Freeze fields in `queue_payload.metadata` (MIGRATIONS = NONE):

- `render_batch_id`
- `render_version` = `campaign-render-v1`
- `final_text_sha256`

Preview concepts:

- A `POST /campaigns/gpt-preview` — GPT suggestion, non-committed
- B `POST /campaigns/render-preview` — sample composition, «پیش‌نمایش نمونه»
- C `RenderedMessage.final_text` — «پیام نهایی ثبت‌شده»

Message log: excerpt list + `GET /campaigns/{id}/recipients/{recipient_id}` full trace.

Queue bridge fail-closed: `RENDER_CONTENT_MISMATCH`.

Evidence: `08_TEST_EVIDENCE.md` — 441 passed on `mmp_phase57_test`.
