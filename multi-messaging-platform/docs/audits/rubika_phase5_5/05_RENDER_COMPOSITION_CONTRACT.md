# Render Composition Contract

Shared service (preview **and** real render — no second renderer):

- `compose_campaign_message(...)`
- `compose_with_locked_products(prose, snapshot)` — Phase 5.6 hook
- `select_and_compose(prose, feed, rng=...)`

## MessageComposition

```
prose_text                  # template / mock / future GPT body
immutable_product_block     # heading + exact names/prices
final_text = prose_text + "\n\n" + immutable_product_block
```

Product facts are **not** concatenated into prose before a future GPT pass.

## Product block

- Always last
- Blank-line separator
- Heading from application allowlist only:

  - `محصولات ویژه امروز:`
  - `محصولاتی که با قیمت استثنایی امروز به فروش می‌رسند:`

- Line format: `{exact name} — {exact formatted price}`

## Pipeline

```
campaign.include_products
→ recipient
→ template variables (prose)
→ fetch advertising products (once)
→ select 3–5 (seeded)
→ freeze snapshot
→ persist RenderedMessage.final_text
→ queue
→ worker sends persisted text
```

Worker must not fetch. GPT must not be called in Phase 5.5 (`real_gpt_called=False`).
