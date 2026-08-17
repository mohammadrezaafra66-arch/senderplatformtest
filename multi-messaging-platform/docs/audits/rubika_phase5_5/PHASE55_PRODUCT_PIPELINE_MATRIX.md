# PHASE55_PRODUCT_PIPELINE_MATRIX

| stage | input | source of truth | mutation allowed? | persisted? | retry behavior | tested? |
|---|---|---|---|---|---|---|
| Campaign config | `include_products` checkbox | `Campaign.include_products` | operator edit while draft | campaign row | n/a | yes (false → no fetch) |
| Product fetch | configured URL or fake provider | AfraKala provider / fake | no (read) | fetch timestamps on snapshot | **not** re-fetched | yes |
| Canonicalize | provider rows | `AdvertisingProduct` | discard invalid only | no (ephemeral) | n/a | yes |
| Advertising filter | explicit tag/bool | canonical `advertising=true` | no inference | no | n/a | yes |
| Freshness | `source_updated_at` or `fetched_at` | settings max staleness | fail closed | timestamps on snapshot | n/a | yes |
| Selection 3–5 | eligible pool + RNG | seeded `random.Random` | only before freeze | selected ids in snapshot | no reselection | yes |
| Freeze snapshot | selected products | `FrozenProductSnapshot` | **never after persist** | `queue_payload.metadata` | reuse snapshot | yes |
| Compose | prose + snapshot | `MessageComposition` | prose only (future GPT) | `final_text` | reuse `final_text` | yes |
| Persist RenderedMessage | composition | DB row | ready/unsent re-prepare only | yes | unchanged | yes |
| Queue / worker | persisted payload | `final_text` / `message_text` | attempt counter only | yes | exact original text | yes |
| Transport | worker message_text | frozen final_text | no | n/a | price X not Y | yes |
| Preview foundation | same compose_* | same snapshot rules | preview flag does not fork renderer | optional | n/a | yes (shared service) |
