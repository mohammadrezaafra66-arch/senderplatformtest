# Remaining Gaps

1. **LIVE_OPENAI_BINDING = CONFIG_PENDING.** Automated tests never call live OpenAI. Bind a real key only for a later authorized synthetic smoke test (no PII, no Rubika send).
2. Phase 5.7 still owns full final-message preview freeze + message-log UX for `variation_id` / `generation_batch_id`.
3. Preview batches are not reused as the committed pool (by design).
4. Legacy `gpt_orchestrator.py` still exists for a separate personalization path and still sends names/products to GPT. Campaign prepare does **not** use it.
5. Unrelated Rubika voice/status bots still import OpenAI; campaign `message_variation` is not imported by workers.
