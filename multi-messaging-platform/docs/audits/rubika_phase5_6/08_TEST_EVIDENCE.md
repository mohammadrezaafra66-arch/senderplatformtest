# Test Evidence

Disposable DB: `mmp_phase56_test` (created, migrated to head, dropped after validation).

## Targeted Phase 5.6
```
python3 -m pytest -q tests/core_engine/test_rubika_phase56_gpt_variation.py tests/api/test_gpt_preview.py
→ 22 passed
```

## Phase 5.5 + campaign + Rubika 1–5
On a dirty shared DB, sender-assignment tests can fail from leftover accounts. On fresh `mmp_phase56_test` they pass as part of the full suite.

## Full backend
```
python3 -m pytest -q -m "not stress and not chaos"
→ 425 passed, 9 deselected, 0 failed
```

## compileall
```
python3 -m compileall -q core_engine workers tests
→ exit 0
```

## Alembic
```
alembic heads  → campaign_accounts_001 (single head)
alembic current on mmp_phase56_test → campaign_accounts_001
MIGRATIONS = NONE
```

## Frontend
```
npx tsc --noEmit → 0
npx eslint src/pages/campaigns/create.tsx src/lib/campaign-api.ts → 0
npm run build → 0
```

## git diff --check
clean
