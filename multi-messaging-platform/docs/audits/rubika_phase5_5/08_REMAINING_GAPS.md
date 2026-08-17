# Remaining Gaps

1. **LIVE_AFRAKALA_BINDING = CONFIG_PENDING.** Real assistant URL/schema/auth is not in the repository. Bind `AFRAKALA_PRODUCT_API_BASE_URL` only after the contract is confirmed.
2. Currency/unit of the live feed is not empirically verified. Adapter config (`AFRAKALA_PRODUCT_PRICE_CURRENCY` / `_DISPLAY_UNIT`) must be set from the real contract — never convert Rial/Toman by guesswork.
3. Phase 5.6 GPT variation is not implemented. `compose_with_locked_products` is the guardrail hook only.
4. Phase 5.7 message-log / full preview UX is not built. Snapshot is persisted and a shared composition service exists for a future preview endpoint.
5. Legacy campaign-level `product_snapshots` (amin-hozoor HTML/cache) still exists for older render/GPT helpers and is **not** the advertising freeze. Do not mix the two sources.
6. `PRODUCT_PRICE_INVALID` is a structured code; per-row invalid prices are discarded rather than failing the whole feed when enough valid products remain.
7. Frontend has no unit-test infra for the new «بررسی محصولات» button; static `tsc` / eslint / build used.
