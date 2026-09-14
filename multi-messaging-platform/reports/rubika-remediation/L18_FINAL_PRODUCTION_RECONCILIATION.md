# L18 Final Production Reconciliation

PHASE_STATUS=COMPLETE  
FINAL_PRODUCTION_RECONCILIATION_PASS=True  
CURRENT_PHASE=L18_FINAL_PRODUCTION_RECONCILIATION_AND_UI_TRUTH

## Deploy provenance

- Deploy script: `scripts/_l18_production_deploy.py` (untracked, hash-locked)
- SHA256 audited/preexec: `a51a590fb7b60403de4b4dd61726d04ae8a7293a81d156c35506b889f11c0961`
- Consumers: `core_api` recreate + `frontend` rebuild/recreate only
- `rubika_worker` / celery: not redeployed (not L18 consumers)
- Override L17 keys: unchanged (`OVERRIDE_UNCHANGED=True`)
- Rollback bak: `docker-compose.override.yml.before-l18-ui-truth-20260901_083312.bak`
- ROLLBACK_PERFORMED=False

## Account truth (48/48 classified)

| Group | IDs |
|-------|-----|
| READY | 13, 23, 27, 74, 79 |
| MANUAL_REVIEW | 2, 12, 19, 81, 92 |
| SESSION_ERROR | 1 |
| LOGIN_REQUIRED | 37 accounts (incl. 3, 28, …) |
| OTP_WAITING | (none) |
| CONNECTION_ERROR | (none) |
| DISABLED | (none) |
| CONNECTED_NOT_READY | (none) |

Known gates:

- 13/23/27/74 → READY (canonical ACTIVE 729/725/772/724)
- 12 → MANUAL_REVIEW (multi-session; pin does not force READY)
- 3 → LOGIN_REQUIRED (zero session; not OTP_WAITING)
- 79 → READY via LEGACY_READY evidence (not fabricated canonical)

## Action probes (no OTP / no send)

- Connection test 27 → success READY
- Connection test 12 → MANUAL_REVIEW (no ambiguous session guess)
- Session/token → no secret leak
- Personal connect routing for account 3 → L3 auto_evidence (`zero_sessions_new_account`) without requesting OTP

## L17 regression

- RUBIKA_L3_LOGIN_ROUTING=auto_evidence
- AUTO_ENROLL_RUBIKA_POOL=true
- RUBIKA_CANONICAL_SESSION_SCOPE=canonical_active
- Workers `[12,13,23,27,74,79]`
- GLOBAL_ACTIVE_SESSION_COUNT=4
- MESSAGE_SENT=False / OTP_REQUESTED=False

## Health

core_api / rubika_worker / frontend / postgres / redis = running; core health 200; frontend HTTP 200.

NEXT_SAFE_ACTION=Close account lifecycle remediation project; remaining LOGIN_REQUIRED or MANUAL_REVIEW accounts are operational work, not software defects.
