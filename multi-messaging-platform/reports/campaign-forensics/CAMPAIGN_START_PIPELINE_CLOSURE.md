# Campaign Start Pipeline Closure

## Root cause

`CONFIRM_VALUE_FIRST_DIVERGENCE_STAGE=START_SERVICE_PREFLIGHT_RECHECK`

Confirm **did** reach the Controlled Production gate as `true`. After the gate passed,
`start_campaign` re-ran `evaluate_campaign_send_preflight`, which intentionally sets
`allowed_to_start=False` with `CONTROLLED_PRODUCTION_APPROVAL_REQUIRED` whenever CP is
on and the campaign is technically ready. Start then treated that advisory UI signal as
a hard blocker and returned the same **409**, so a successful Confirm looked identical
to a missing Confirm.

Evidence match: production response body size (~406) aligned with the **Persian**
preflight rejection payload (blockers + `execution_safety_state`), not the shorter
English gate-only payload (~250).

## Fix

In `campaign_control.start_campaign`, when
`confirm_controlled_production=true` **and**
`preflight.controlled_production_confirmation_required=true`, treat the CP advisory
blocker as satisfied and proceed (technical readiness is implied by that flag).

Also added:

- Start `request_id` (`X-Request-Id` / generated UUID)
- Structured safe Start pipeline logs
- Explicit Start response contract fields
- Frontend always serializes `confirm_controlled_production` as a boolean
- Duplicate-confirm UI guard (`startConfirmLoading || actionLoading`)

Controlled Production remains authoritative. No auto-confirm. No campaign/account hardcodes.

## Tests (isolated)

- Hermetic + HTTP + E2E hermetic: **29 passed**
- Frontend contract/proxy/UI: **20 passed**

## Deploy

- `mmp_core_api` restarted (bind-mounted `core_engine`)
- `mmp_frontend` rebuilt + recreated
- Worker unchanged

## Canary precheck (read-only)

Campaign **127**: prepared, 1 recipient, 1 ready message, sender 79 enabled,
0 attempts, staged ready, Redis queue empty.

**REAL send requires separate operator approval.**
