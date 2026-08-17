# Retry and restart invariants

Retry (`build_retry_queue_payload`) only increments `attempt`. It does not call GPT or the product provider and does not re-render the template.

Restart: a new DB session loads `StagedQueueItem.queue_payload.final_text`. Transport mock receives that exact string.

Queue bridge verifies:

- `queue_payload.final_text` equals `Message.rendered_text` when the message exists
- SHA-256 matches `metadata.final_text_sha256` when present (pre-5.7 rows without a hash are not failed)
- If `message_text` is also present, it must equal `final_text`

Mismatch → skip, `skip_reason=RENDER_CONTENT_MISMATCH`, no transport.
