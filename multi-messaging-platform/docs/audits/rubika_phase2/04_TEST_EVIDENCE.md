# Test Evidence

Primary: `tests/core_engine/test_rubika_phase2_preflight.py`

| Group | Coverage |
|-------|----------|
| A preflight unit | ready, banned, login, resting, wrong platform, user disabled, session missing, campaign deny, window, pool, cooldown, hourly, Redis fail-closed |
| B bot_api connector | blocked states + transport_call_count==0; valid path count==1 |
| C user_account connector | disabled/missing blocked; valid once; no silent pool replace |
| Regression | existing rubika connector + user connector suites |

Isolation: disposable Postgres, pinned `RUBIKA_DELIVERY_MODE`, Redis reset fixture, mocked transport.
