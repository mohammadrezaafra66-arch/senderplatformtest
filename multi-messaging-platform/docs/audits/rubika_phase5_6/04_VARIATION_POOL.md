# Variation Pool

Generated **once** per prepare (or reused if an existing staged payload has the same `template_fingerprint`).

Each variation: `variation_id`, `text`, `provider`, `model`, `generated_at`, `generation_batch_id`.

Persisted on every `RenderedMessage.queue_payload.metadata.gpt_variation_pool` plus assignment `gpt_variation` (MIGRATIONS = NONE).

Recipient assignment: `sha256(campaign_id:contact_id:generation_batch_id) % pool_size`.

Draft + ready (unsent): template fingerprint change regenerates a new batch.
Queued/sent: never mutated; retry does not call GPT.

Preview is **not** trusted as the committed pool (no browser-posted variation text).
