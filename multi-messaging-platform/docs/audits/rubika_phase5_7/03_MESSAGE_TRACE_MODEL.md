# Message trace model

Authoritative send text (priority):

1. `Message.rendered_text` (copied from compose at prepare; used after send attempt linking)
2. `RenderedMessage.final_text` / `StagedQueueItem.final_text` / `queue_payload.final_text` (same string at prepare)

Queue payload holds `final_text` plus `metadata`:

- `render_batch_id`, `render_version`, `final_text_sha256`
- `gpt_variation` (provider, model, `generation_batch_id`, `variation_id`) and frozen pool for re-prepare reuse
- `frozen_product_snapshot` (exact name/price/ids/timestamps)

List API never returns the full snapshot or full text for every row. Detail API returns full text + public GPT/product traces. No API keys, no raw provider JSON, no system prompt.
