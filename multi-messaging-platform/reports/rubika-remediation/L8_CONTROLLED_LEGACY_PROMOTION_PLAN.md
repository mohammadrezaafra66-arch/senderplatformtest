# L8 — Controlled Legacy Promotion Plan (Batch A)

**PHASE_STATUS: BLOCKED pending operator approval for production**

## Implemented

- `promote_proven_legacy_rubika_session` (legacy → prove → bind → VALIDATING → `promote_validated_session`)
- Exact-ID `rollback_legacy_promotion`
- Isolated tests + clone rehearsal

## Rehearsal summary

- backup: `C:\Users\poorchista\senderplatformtest\multi-messaging-platform\reports\rubika-remediation\l8_rehearsal\mmp_db_pre_l8_20260831T075848Z.dump`
- sha256: `ae07b69862e39fd9ac6fece64a32ffc6a7eb1b28b0c8e23f417063fab911153e`
- rehearsal promotions ok: `True`
- non-target ACTIVE count: `0`
- active_total on clone: `3`

## Production execution order (NOT executed yet)

1. Fresh production backup
2. Account13 / Session729 — promote + verify + STOP
3. Account23 / Session725 — promote + verify + STOP
4. Account74 / Session724 — promote + verify + STOP
5. Only then set `RUBIKA_CANONICAL_SESSION_MODE=shadow` with allowlist `13,23,74`
6. Observe SHADOW_MATCH; do **not** enable enforce

## Hard exclusions

- No Account2 / 12 / 79 / 1 / 19 / 81 / 92
- No global enforce
- No OTP / send / worker restart during promotion

**SAFE_TO_PROMOTE_BATCH_A_PRODUCTION** (after rehearsal): `True`

**EXACT_OPERATOR_INPUT_REQUIRED:** Approve production promotion of Account13/23/74 one at a time after rehearsal

