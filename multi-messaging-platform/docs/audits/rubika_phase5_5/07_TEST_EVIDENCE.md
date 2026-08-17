# Test Evidence

Disposable DB: `postgresql://mmp_user:mmp_pass@localhost:5432/mmp_phase55_test`  
Mocked AfraKala provider. No external product network. No real Rubika messages.

## Targeted Phase 5.5
```
python3 -m pytest -q tests/core_engine/test_rubika_phase55_product_feed.py tests/api/test_product_feed_status.py
→ 21 passed
```

Coverage includes: valid feed, timeout, connection failure, invalid JSON/shape, missing id/name, invalid/float price, explicit currency, advertising=false excluded, stale source, mixed valid/invalid, fewer than 3 eligible, selection 3–5, no duplicates, recipient variation, freeze/retry, include_products=false call_count=0, feed failure blocks queue, GPT-safe prose/product split.

## Campaign / render regressions
```
python3 -m pytest -q tests/api/test_campaign_sender_assignment.py tests/api/test_campaign_template_render.py
→ included in the 96-pass Rubika+campaign run below
```

## Rubika Phase 1–5.5 regressions
```
python3 -m pytest -q \
  tests/api/test_campaign_sender_assignment.py \
  tests/api/test_campaign_template_render.py \
  tests/core_engine/test_rubika_phase1_account_session.py \
  tests/core_engine/test_rubika_phase2_preflight.py \
  tests/core_engine/test_rubika_phase3_rate_lifecycle.py \
  tests/core_engine/test_rubika_phase4_health_circuit.py \
  tests/core_engine/test_rubika_phase5_operations.py \
  tests/api/test_rubika_phase5.py
→ 96 passed
```

## Full backend
```
dropdb/createdb mmp_phase55_test && alembic upgrade head
python3 -m pytest -q -m "not stress and not chaos"
→ 403 passed, 9 deselected, 0 failed
```

## compileall
```
python3 -m compileall -q core_engine workers tests
→ exit 0
```

## Alembic
```
alembic heads  → campaign_accounts_001 (head)  [single head]
alembic current on mmp_phase55_test → campaign_accounts_001 (head)
MIGRATIONS = NONE for Phase 5.5
```

## git diff --check
```
→ clean
```

## Frontend
```
npx tsc --noEmit → exit 0
npx eslint src/pages/campaigns/create.tsx src/lib/campaign-api.ts → exit 0
npm run build → exit 0
```
