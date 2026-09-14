# Controlled Production — Final Reconciliation

## Closure strategy
Production Start was **intentionally not clicked**. Verification used:

1. Isolated backend tests on `mmp_test_isolation` (20 passed)
2. Isolated frontend + modal tests (19 passed)
3. Exact frontend image/bundle redeploy + SHA256 match
4. Live read-only browser CDP text for campaigns 125/126/127

## Live campaigns 125 / 126 / 127
All three show:

- Preparation: آماده ارسال
- Controlled banner: حالت ارسال کنترل‌شده فعال است — تأیید نهایی برای هر شروع لازم است.
- Execution readiness: READY
- Start button rendered (not clicked)

No opaque deadlock: UI does **not** claim immediate start without confirmation while backend would 409.

## Global prepared state
Prior production simulation (`CONTROLLED_PRODUCTION_TEST_MATRIX` / readonly simulation) recorded:

- opaque_controlled_gate_deadlocks = 0
- unexpected_behavior_changes = 0

Live UI sampling of the three historically deadlocked campaigns confirms the UI now surfaces the confirmation requirement explicitly.

## Future normal ops (documentation only)
To later disable controlled production:

1. Prerequisites: stable send path, worker coverage healthy, no unexplained 409s, operator trained on Start UX.
2. Set `CONTROLLED_PRODUCTION_ENABLED=false` in compose override / env for core_api (and worker if mirrored).
3. Redeploy/restart core_api only.
4. Remaining guards: preflight, capacity, session readiness, max messages, circuit, quota.
5. Rollback: set flag true again and restart core_api.

**Do not change the flag in this phase.**

## Final
`FINAL_CONTROLLED_PRODUCTION_GATE_PASS=True`

`NEXT_SAFE_ACTION=Operator may perform a separately approved real Campaign Start through the new confirmation modal.`
