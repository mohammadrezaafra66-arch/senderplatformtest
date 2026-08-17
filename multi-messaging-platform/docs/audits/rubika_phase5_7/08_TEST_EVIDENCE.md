# Test Evidence

Disposable DB: `mmp_phase57_test` (created, migrated to `campaign_accounts_001`, used only for this phase).

No live OpenAI, AfraKala, or Rubika send.

## Targeted Phase 5.7
```
python3 -m pytest -q tests/core_engine/test_rubika_phase57_render_trace.py tests/api/test_phase57_message_log.py
→ 16 passed
```

## Phase 5.5 + 5.6 + prepare + queue + recipients
```
python3 -m pytest -q tests/core_engine/test_rubika_phase55_product_feed.py tests/core_engine/test_rubika_phase56_gpt_variation.py tests/api/test_gpt_preview.py tests/api/test_campaign_template_render.py tests/api/test_campaign_recipients.py tests/queue_bridge/ tests/core_engine/test_rubika_phase57_render_trace.py tests/api/test_phase57_message_log.py
→ 99 passed
```

Sender-assignment tests fail on a dirty shared DB (leftover accounts). On a freshly created `mmp_phase57_test` they pass as part of the full suite.

## Full backend
```
python3 -m pytest -q -m "not stress and not chaos"
→ 441 passed, 9 deselected, 0 failed
```

## compileall
```
python3 -m compileall -q core_engine workers tests
→ exit 0
```

## Alembic
```
alembic heads   → campaign_accounts_001 (single head)
alembic current on mmp_phase57_test → campaign_accounts_001
MIGRATIONS = NONE
```

## Frontend
```
npx tsc --noEmit → 0
npx eslint <changed files> → 0
npm run build → 0
```

No frontend unit runner (no Jest/Vitest). XSS safety is React text/`<pre>` rendering.

## git diff --check
```
exit 0
```
