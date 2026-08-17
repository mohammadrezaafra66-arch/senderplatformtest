# 10 REMAINING GAPS

- Owner must identify one production Rubika account and one consented recipient.
- LIVE_AFRAKALA_BINDING CONFIG_PENDING (no URL/token/contract; price unit empirically UNKNOWN).
- LIVE_OPENAI_BINDING CONFIG_PENDING (no API key in this environment).
- Product-enabled and GPT-enabled live pilots are not allowed until those bindings and Pilot 1 succeed.
- `CAMPAIGN_DISPATCH_INTERVAL_MS` still unused as a sleep (Phase 6 gap).
- Compose `rubika_worker` defaults `DRY_RUN=true` if host env omits DRY_RUN — operators must set host `.env` explicitly for PILOT_LIVE.
- `/health` is liveness-only (no DB/Redis probe).
- No Vitest UI tests.
- Do not use `scripts/rubika_user_send_test.py` as the production path; it now requires confirmation flags and the worker router, but campaign→queue→worker remains the approved path.
