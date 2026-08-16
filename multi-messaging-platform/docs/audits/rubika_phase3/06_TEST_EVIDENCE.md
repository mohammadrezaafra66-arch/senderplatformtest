# Phase 3 Test Evidence

## Isolation

- Disposable DB: `mmp_phase3_test`
- Flags: `REAL_QUEUE_PUSH_ENABLED=false`, `REAL_MESSAGE_SENDING_ENABLED=false`,
  `CHANNEL_CONNECTORS_ENABLED=false`
- No wall-clock sleeps for assertions; clock/RNG injected where needed
- No real Rubika recipients or live sends

## Targeted

`tests/core_engine/test_rubika_phase3_rate_lifecycle.py`

Covers: lifecycle, daily/hourly, min interval, jitter, concurrency reservation,
commit/release, send window boundaries + overnight, cooldown/throttle,
Redis fail-closed, transport_not_called proofs, assigned-account authority.

## Regression

- `tests/core_engine/test_rubika_phase2_preflight.py`
- `tests/core_engine/test_rubika_phase1_account_session.py`
- `tests/workers/test_rubika_connector.py`
- `tests/workers/test_rubika_user_connector.py`

## Full suite / tooling

Recorded in final Phase 3 report after run:

- `python -m pytest -q -m "not stress and not chaos"`
- `python -m compileall -q core_engine workers tests`
- `alembic heads` → single head
- `git diff --check`
