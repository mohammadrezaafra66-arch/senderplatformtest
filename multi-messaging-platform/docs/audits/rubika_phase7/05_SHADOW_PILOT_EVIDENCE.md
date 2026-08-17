# 05 SHADOW PILOT EVIDENCE

Profile: PILOT_SHADOW (`REAL_MESSAGE_SENDING_ENABLED=false`).

Automated evidence: `tests/core_engine/test_rubika_phase7_release_readiness.py`

| Check | Result |
|---|---|
| prepare | pass (plain template, GPT OFF, products OFF) |
| final_text frozen | pass |
| hash | SHA-256 stable across new DB session (restart-safe) |
| sender | persisted `Message.account_id` unchanged |
| preflight/capacity | Phase 6 services available; no live start |
| queue simulation | worker router invoked with kill switch OFF |
| transport_blocked | `real_send_disabled`; connector mocks not called |
| GPT/product refetch | not invoked (flags off) |

No real Rubika transport executed.
