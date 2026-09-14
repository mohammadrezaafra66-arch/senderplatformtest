# Controlled Production Gate Fix — Final Closure

## Root cause
Frontend `startCampaign()` sent an empty POST body while Rubika Start required
`confirm_controlled_production=true` when `CONTROLLED_PRODUCTION_ENABLED=true`.
Preflight could report ready while Start returned HTTP 409.

## Product decision (unchanged)
Keep controlled production enabled. Per-Start explicit operator confirmation only.
Do not persist approval. Confirmation is not a safety bypass.

## Contract
### Backend Start
`CampaignStartRequest.confirm_controlled_production: bool = False`
Gate in `start_campaign()` before technical preflight when Rubika + CP enabled.

### Preflight parity
When technically ready + CP + Rubika:
- `technical_ready=true`
- `controlled_production_confirmation_required=true`
- `allowed_to_start=false`
- `allowed_to_start_after_confirmation=true`
- Persian `controlled_production_label`

### Frontend
- Start opens Persian `ConfirmDialog` when confirmation required
- Confirm sends `{ "confirm_controlled_production": true }` once
- Cancel sends zero Start requests
- Raw English codes translated via `campaign-start-errors.ts`

## Verification method (approved)
Production Start click intentionally **not** performed.

Proof layers:
1. Isolated backend tests (mmp_test_isolation, not live mmp_core_api) — **20 passed**
2. Isolated frontend/modal tests (vitest + renderToStaticMarkup) — **19 passed**
3. Exact tested frontend artifact redeployed and hash-matched
4. Live GET/browser read-only UI for campaigns 125/126/127

`LIVE_MODAL_PROOF_METHOD=EXACT_TESTED_ARTIFACT_EQUIVALENCE`

## Safety
- CONTROLLED_PRODUCTION_ENABLED remains true
- No real Start POST
- No queue push / MessageAttempt / external send during verification
- No session/account/worker-pin/DB migration changes

## Source manifest
See `CONTROLLED_SOURCE_MANIFEST.sha256`
COMBINED: `ecf21f912c46e367696cc65a18dc01c46dba162d2ace55c5052b7a880f7e5bbe`

## Next safe action
Operator may perform a **separately approved** real Campaign Start through the new confirmation modal.
