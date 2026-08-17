# Render freeze contract

`render_version`: `campaign-render-v1`  
`render_batch_id`: UUID hex, shared by every `RenderedMessage` created or refreshed in one `prepare_campaign_messages` call.  
`final_text_sha256`: SHA-256 of canonical UTF-8 `final_text`. Stored **in addition to** the text.

## Pipeline (single service)

`core_engine.services.campaign_render.compose_final_render`

1. Choose prose: base template, assigned GPT variation, or mock (explicit flag).
2. Substitute recipient placeholders (`{{first_name}}` / `last_name` / `full_name` / `name`). Unknown tokens stay visible.
3. If `include_products`: freeze a per-recipient snapshot and append the locked product block last.
4. Hash + metadata.

Prepare, sample preview, and (for GPT suggestions) product-block append all reuse this composer or `compose_with_locked_products` for the product tail.

## Freeze boundary

Before prepare: draft campaign/template may change.  
On prepare: GPT assignment, product snapshot, placeholders, `final_text`, hash, sender `account_id`, metadata are persisted.  
After prepare:

- `queued` / `sent` staged rows are never rewritten.
- Unsent `ready` rows may be replaced by an **explicit re-prepare**, which is a new `render_batch_id` (transactional).
- Sent `Message.rendered_text` / historical queued payloads are not mutated.

## Template edit

There is no public template-edit API after create. If `campaign.template_text` is changed in DB:

- Re-prepare refreshes `ready` items to the new template (new batch).
- `queued`/`sent` keep the old exact text.

UI must not present unprepared or stale drafts as «پیام نهایی ثبت‌شده». Campaign detail only shows DB `RenderedMessage` samples.

## Re-prepare

Explicit re-prepare of unsent ready rows is all-or-nothing: GPT/product failure or mid-loop render failure rolls back so no half-frozen batch remains.

## Preview → commit

Not implemented. Sample preview is conceptual; prepare generates the committed batch. Frontend-posted `final_text` is never accepted as committed content.
