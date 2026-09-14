# L16 — New Account Automation Gap Audit

**FULL_AUTOMATIC_NEW_ACCOUNT_LIFECYCLE_READY=False**

**REMAINING_AUTOMATION_GAP=RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS / RUBIKA_CANONICAL_SESSION_V1; AUTO_ENROLL_RUBIKA_POOL + RubikaAccountPool membership; RUBIKA_WORKER_DISCOVERY_COHORT_IDS; RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS**

## Readiness matrix

- NEW_ACCOUNT_AUTO_LOGIN_L3_READY=False
- NEW_ACCOUNT_AUTO_SESSION_PROMOTION_READY=True
- NEW_ACCOUNT_AUTO_POOL_READY=False
- NEW_ACCOUNT_AUTO_DISCOVERY_READY=False
- NEW_ACCOUNT_AUTO_WORKER_COVERAGE_READY=False
- NEW_ACCOUNT_AUTO_CANONICAL_ENFORCE_READY=False

## Live config snapshot

```json
{
  "login_pilot": [
    "27"
  ],
  "V1_override": [],
  "AUTO_ENROLL_override": [],
  "discovery_mode": [
    "dynamic"
  ],
  "cohort": [
    "13,23,27,74"
  ],
  "canonical_mode": [
    "enforce",
    "enforce",
    "enforce"
  ],
  "allowlist": [
    "13,23,27,74",
    "13,23,27,74",
    "13,23,27,74"
  ],
  "v1_default_false": true,
  "auto_enroll_default_false": true
}
```

## Gap: `RUBIKA_CANONICAL_LOGIN_PILOT_ACCOUNT_IDS / RUBIKA_CANONICAL_SESSION_V1`

- CURRENT_BEHAVIOR: V1 default False; override V1 unset; pilot=['27']. Only listed account IDs use L3 request/submit login.
- WHY_MANUAL: Temporary L16 pilot gate to avoid mass cutover of legacy login.
- DESIRED_FINAL_BEHAVIOR: RUBIKA_CANONICAL_SESSION_V1=true (or empty pilot meaning all accounts) so any new Rubika account uses L3 login automatically.
- SAFE_REMOVAL_STRATEGY: Enable V1 globally after L3 proven on pilot set; keep legacy fence; staged enable with rollback to pilot list.
- TESTS_REQUIRED: ['test_l16_login_pilot_gate', 'L3 request/submit for non-pilot blocked vs V1-all allowed', 'legacy login fence when V1 true']

## Gap: `AUTO_ENROLL_RUBIKA_POOL + RubikaAccountPool membership`

- CURRENT_BEHAVIOR: AUTO_ENROLL_RUBIKA_POOL default/false; login success does not enroll pool. Discovery requires phase pool membership.
- WHY_MANUAL: Safety: prevent accidental campaign capacity from login alone.
- DESIRED_FINAL_BEHAVIOR: Optional controlled auto-enroll after auth_ready+identity verified, or explicit post-login enrollment workflow without hand-editing pool rows.
- SAFE_REMOVAL_STRATEGY: Feature-flag AUTO_ENROLL_RUBIKA_POOL for verified accounts only; start with shadow enroll logs.
- TESTS_REQUIRED: ['evaluate_dispatch_ready_readonly NOT_IN_POOL', 'auto-enroll creates exact phase row only when flag true']

## Gap: `RUBIKA_WORKER_DISCOVERY_COHORT_IDS`

- CURRENT_BEHAVIOR: mode=['dynamic'] cohort=['13,23,27,74']. Non-empty cohort filters dynamic eligible set; new account IDs must be added manually.
- WHY_MANUAL: Temporary staged rollout (L12–L16) to limit blast radius.
- DESIRED_FINAL_BEHAVIOR: RUBIKA_WORKER_DISCOVERY_MODE=dynamic with empty cohort (all dispatch-eligible accounts covered) OR auto-append on auth_ready.
- SAFE_REMOVAL_STRATEGY: Empty cohort after Batch+pilot stable; monitor worker count; keep pin for legacy 12/79 until separately retired.
- TESTS_REQUIRED: ['get_dispatch_eligible with cohort=[] vs non-empty', 'resolve_actual_worker_account_ids empty cohort includes new eligible id']

## Gap: `RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS`

- CURRENT_BEHAVIOR: mode=['enforce', 'enforce', 'enforce'] allowlist=['13,23,27,74', '13,23,27,74', '13,23,27,74']. Enforce applies only to listed IDs; new accounts remain legacy max(id) until manually allowlisted.
- WHY_MANUAL: Temporary staged ENFORCE rollout (L15–L16).
- DESIRED_FINAL_BEHAVIOR: After fleet proof: enforce for all accounts with ACTIVE canonical+identity, or auto-allowlist on successful L3 promotion.
- SAFE_REMOVAL_STRATEGY: Expand allowlist in batches; eventually switch to enforce-all policy with explicit deny-list if needed; never silent max(id) fallback.
- TESTS_REQUIRED: ['enforce_applies_to_account', 'load_rubika_runtime_session source=canonical_enforce fail-closed']

## Boundary

This audit does **not** remove rollout gates. L16 only identifies the gap.
