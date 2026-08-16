# Test Evidence

## Targeted Phase 5
```
DATABASE_URL=postgresql://mmp_user:mmp_pass@localhost:5432/mmp_phase5_test
python3 -m pytest -q tests/core_engine/test_rubika_phase5_operations.py tests/api/test_rubika_phase5.py
→ 8 passed
```

## Rubika Phase 1–5 regressions (sample)
```
python3 -m pytest -q tests/core_engine/test_rubika_phase5_operations.py tests/api/test_rubika_phase5.py \
  tests/core_engine/test_rubika_phase4_health_circuit.py tests/core_engine/test_rubika_phase3_rate_lifecycle.py
→ 32 passed
```

## Full backend
```
DATABASE_URL=…/mmp_phase5_clean (fresh disposable)
python3 -m pytest -q -m "not stress and not chaos"
→ 382 passed, 9 deselected, 0 failed
```

## compileall
```
python3 -m compileall -q core_engine workers tests
→ exit 0
```

## Alembic
```
alembic heads → campaign_accounts_001 (head)  [single head]
alembic current on disposable → campaign_accounts_001 (head)
MIGRATIONS = NONE for Phase 5
```

## Frontend
```
npx tsc --noEmit → exit 0
npx eslint <changed FE files> → exit 0
npm run build → exit 0
```

## git diff --check
Recorded at commit time.
