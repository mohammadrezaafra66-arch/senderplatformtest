# 04 PILOT ACCOUNT READINESS

Do not login or mutate sessions in this phase.

## Environment

This cloud pod has **no production `mmp_db`**. Rubika rows exist only in disposable pytest databases (`mmp_phase6_test`, etc.). Labels such as `p4ok` / `api_ov` are test fixtures.

They are **not** owner-identified safe pilot accounts.

## Safe inventory (masked)

Disposable DB leftovers only. Example shape (not eligible):

| account_id | label | masked phone | mode | status | eligible for pilot? |
|---|---|---|---|---|---|
| (fixture) | p4ok | 989******xxx | bot_api (env) | ACTIVE | NO — pytest leftover, not owner-approved |

## Selection

Pilot must use **one** owner-designated account that is:

- platform Rubika
- ACTIVE
- session ready
- HEALTHY
- not quarantined
- circuit CLOSED
- policy eligible
- not used by another uncontrolled sender

**Recommended candidate:** none in this environment.

**selected_account:** pending owner identification on the real operator database.

## Session

Not validated against an owner account (none selected). Readiness APIs exist (`evaluate_account_session_readiness`).

## Duplicate sender process

This pod: no Docker compose, no `run_forever` / celery / uvicorn processes.

Production compose always starts `rubika_worker`. For Pilot 1 run **one** replica of `rubika_worker` only. Listener/status/ai-loop profiles must stay down for the pilot account.

## Circuit baseline (this Redis)

- state: CLOSED
- Redis: reachable
- opened_at metadata may exist from prior tests; circuit was **not** forced CLOSED for this phase
