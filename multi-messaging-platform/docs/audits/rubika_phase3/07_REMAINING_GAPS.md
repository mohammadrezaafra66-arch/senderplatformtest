# Remaining Gaps (Phase 3)

1. **Side-channel reservation** — AI / listener / status enforce Layer F deny
   but do not yet Lua-reserve before transport (low volume; tracked for Phase 4).
2. **bot_api warm-up policy** — intentional omission; Bot API uses official
   platform limits. Application warm-up applies to user_account.
3. **Weighted failover** — explicitly out of scope (would violate assigned-account
   authority if added opportunistically).
4. **Circuit breaker / health** — Phase 4.
5. **Dev scripts** EP12/EP14 remain non-production residuals (documented).
6. **Frontend ops UI** for policy snapshot — observability fields exist in
   preflight `details.policy`; UI wiring is later work.
