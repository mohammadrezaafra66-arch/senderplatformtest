# L7 — Canonical Cohort Gating + Duplicate Session Probes

**Generated:** 2026-08-31T07:51:00.183293+00:00
**Mutation:** False
**OTP requested:** False
**Messages sent:** False
**Promotions:** 0

## Runtime modes

- `RUBIKA_CANONICAL_SESSION_MODE=off|shadow|enforce`
- `RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS=` cohort allowlist
- Default remains **off** (legacy). Global enforce is not the first rollout path.
- Shadow is non-mutating. Enforce fails closed for allowlisted accounts.

## Duplicate probe classifications

### Account 2
- classification: `ONE_PROVEN_ONE_INVALID`
- legacy_selected: `721`
  - session `2` decrypt=SESSION_DECRYPT_FAILED structure=N/A reconnect=SKIPPED identity_match=None legacy_selected=False
  - session `721` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=True

### Account 12
- classification: `BOTH_VALID_CURRENT_IDENTITY`
- legacy_selected: `728`
  - session `657` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=False
  - session `728` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=True

### Account 19
- classification: `BOTH_VALID_SAME_IDENTITY`
- legacy_selected: `727`
  - session `726` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=False
  - session `727` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=True

### Account 81
- classification: `BOTH_VALID_SAME_IDENTITY`
- legacy_selected: `723`
  - session `722` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=False
  - session `723` decrypt=OK structure=OK reconnect=AUTH_RECONNECT_PASS identity_match=True legacy_selected=True

### Account 92
- classification: `DECRYPT_REPAIR_OR_RELOGIN_REQUIRED`
- legacy_selected: `12`
  - session `11` decrypt=SESSION_DECRYPT_FAILED structure=N/A reconnect=SKIPPED identity_match=None legacy_selected=False
  - session `12` decrypt=SESSION_DECRYPT_FAILED structure=N/A reconnect=SKIPPED identity_match=None legacy_selected=True

## Batch A verification

- Account 13: candidate=`729` verified=True
- Account 23: candidate=`725` verified=True
- Account 74: candidate=`724` verified=True

## Account79 protected

- candidate: `600`
- verified: `True`

## Recommendations (NO PROMOTION)

- BATCH_A_FINAL: [13, 23, 74]
- BATCH_B_FINAL: [2]
- MANUAL_REVIEW_FINAL: [1, 12, 19, 81, 92]
- ACCOUNT12_RECOMMENDED_CANDIDATE: 728
- ACCOUNT12_SAFE_TO_PROMOTE: False

## Next safe action

Review L7 before first controlled canonical session promotion.

