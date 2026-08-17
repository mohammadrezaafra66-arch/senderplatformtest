# Product Immutability

GPT never receives the advertising product list or frozen snapshot.

Pipeline when both flags are on:

template → GPT pool → assign variation → substitute placeholders → Phase 5.5 select/freeze → `compose_with_locked_products` / `select_and_compose`.

Locked block (names, prices, unit, heading) comes only from the Phase 5.5 formatter. Headings remain the application allowlist.

GPT-invented product names/prices may appear in **prose** if the model ignores instructions; they **cannot** replace the locked block.
