# 09 TEST EVIDENCE

Targeted Phase 6:

- `tests/core_engine/test_rubika_phase6_campaign_capacity.py`
- `tests/queue_bridge/test_phase6_controlled_dispatch.py`

Result: **23 passed**.

Full backend:

```
python3 -m pytest -q -m "not stress and not chaos"
464 passed, 9 deselected, 4131 warnings
```

Disposable DB: `mmp_phase6_test` (dropped, created, migrated to `campaign_accounts_001`).

Isolated Redis: FakeRedis / fakeredis / in-process BridgeRedis.

Transport: mocked (`send_count` / no network).

compileall: OK (`core_engine workers tests`).

alembic heads: `campaign_accounts_001` (single head).

alembic current on `mmp_phase6_test`: `campaign_accounts_001`.

Frontend: `npx tsc --noEmit`, eslint on changed TS/TSX files, `npm run build`.

No Jest/Vitest campaign-detail component runner in this repo.

REAL RUBIKA MESSAGE SENT: NO.
