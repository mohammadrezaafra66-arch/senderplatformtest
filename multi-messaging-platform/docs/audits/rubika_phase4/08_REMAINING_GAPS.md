# Remaining Gaps

1. Circuit Redis state is ephemeral across Redis wipe (preflight still fail-closed
   when Redis unavailable).
2. Health window counters are Redis-only (restart loses consecutive count).
3. Side channels still lack Lua quota reservation (Phase 3 residual).
4. No frontend Protection Center UI (Phase 5).
5. Campaign-level pause on circuit OPEN relies on retryable worker results +
   existing pause/kill patterns — no dedicated campaign auto-pause mutation
   (intentional: reversible gating).
6. HALF_OPEN live probes are mocked in tests only — production probes are real
   sends that already passed preflight (no separate network probe channel).
