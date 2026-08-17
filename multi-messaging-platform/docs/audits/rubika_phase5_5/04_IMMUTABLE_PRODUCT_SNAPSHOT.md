# Immutable Product Snapshot

## When freeze happens

Campaign prepare / render time, **before** queueing:

1. Fetch advertising feed once per prepare
2. Per recipient, select 3–5 products
3. Build `FrozenProductSnapshot`
4. Compose `final_text` from prose + locked product block
5. Persist snapshot on `RenderedMessage.queue_payload.metadata`

## Storage

No new table (MIGRATIONS = NONE).

```
RenderedMessage.queue_payload.metadata.frozen_product_snapshot
RenderedMessage.queue_payload.metadata.prose_text
RenderedMessage.queue_payload.metadata.immutable_product_block
RenderedMessage.final_text
```

Relationship is **message-specific**, not campaign-current-selection.

Legacy `product_snapshot_id` (campaign-level amin-hozoor cache) is **not** the Phase 5.5 freeze. It remains unused (`null`) on this path.

## After freeze

- No later API refresh mutates that message
- Worker never calls AfraKala
- Retry uses persisted `final_text` / `message_text`
- Future GPT (Phase 5.6) may rewrite `prose_text` only via `compose_with_locked_products`

## Re-prepare boundary

| State | Re-prepare |
|---|---|
| Staged `ready` (never queued) | May regenerate a new snapshot (draft/unsent) |
| `queued` / `sent` | **Not mutated** |

Committed render = staged item has left `ready`.
