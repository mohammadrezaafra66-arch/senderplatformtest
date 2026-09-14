# M1 — Manual-Review Forensic Audit

Generated: 2026-09-01T09:41:03.068966+00:00
READ_ONLY_AUDIT_PASS=True
M1_READ_ONLY_INVARIANT_PASS=True
PHASE_STATUS=COMPLETE
MAX_ID_SELECTION_USED=False

## Safety gates
- DB_READ_ONLY_ENFORCED=True
- REDIS_READ_ONLY=True
- RUBIKA_AUTH_PROBE_READ_ONLY=True
- RUBIKA_AUTH_PROBE_PERSISTS_SESSION=False
- M1_NETWORK_MUTATION_PRESENT=False
- M1_NETWORK_CALLS=['rubpy.Client.connect', 'rubpy.Client.get_me', 'rubpy.Client.get_user_info', 'rubpy.Client.get_abs_objects', 'rubpy.Client.disconnect']

## Invariants
- GLOBAL_ACTIVE_SESSION_COUNT=4
- ACTIVES={13: 729, 23: 725, 27: 772, 74: 724}
- ACTUAL_WORKER_IDS=[12, 13, 23, 27, 74, 79]
- SESSION_ROWS_MUTATED=0
- SESSION_STATUS_CHANGES=0
- LOGIN_CHALLENGE_DELTA=0
- MESSAGE_ATTEMPT_DELTA=0
- MESSAGE_SENT=False
- OTP_REQUESTED=False

## Per-account
### Account 2
- Classification: **ONE_PROVEN_ONE_INVALID**
- Historical confirmed: True
- Sessions: [2, 721]
- Decrypt matrix: 2=DECRYPT_FAILED/UNKNOWN, 721=DECRYPT_OK/STRUCTURALLY_VALID
- Auth PASS: [721]
- Auth FAIL: []
- Auth INDETERMINATE: []
- Decrypt failed: [2]
- Identity relation: N/A_SINGLE_VALID
- Worker covered/pinned: False/False
- MANUAL_REVIEW_BLOCKER: LEGACY_MULTI_SESSION
- Runtime: MANUAL_REVIEW (LEGACY_MULTI_SESSION)
- Proposed authoritative: 721
- OTP required: False
- Operator decision: False
- Canonicalization safety: SAFE_TO_CANONICALIZE_AFTER_SELECTING_PROVEN_SESSION
- Remediation: Promote proven session 721 after operator approval; retire invalid sibling without deleting evidence until approved.
- Risk: LOW

### Account 12
- Classification: **MULTIPLE_VALID_SAME_IDENTITY**
- Historical confirmed: True
- Sessions: [657, 728]
- Decrypt matrix: 657=DECRYPT_OK/STRUCTURALLY_VALID, 728=DECRYPT_OK/STRUCTURALLY_VALID
- Auth PASS: [657, 728]
- Auth FAIL: []
- Auth INDETERMINATE: []
- Decrypt failed: []
- Identity relation: SAME_IDENTITY
- Worker covered/pinned: True/True
- MANUAL_REVIEW_BLOCKER: LEGACY_MULTI_SESSION
- Runtime: MANUAL_REVIEW (LEGACY_MULTI_SESSION)
- Proposed authoritative: None
- OTP required: False
- Operator decision: True
- Canonicalization safety: REQUIRES_SESSION_RETIREMENT_DECISION
- Remediation: Same identity duplicates with no deterministic non-max(id) winner — operator must select authoritative session.
- Risk: MEDIUM

### Account 19
- Classification: **MULTIPLE_VALID_SAME_IDENTITY**
- Historical confirmed: True
- Sessions: [726, 727]
- Decrypt matrix: 726=DECRYPT_OK/STRUCTURALLY_VALID, 727=DECRYPT_OK/STRUCTURALLY_VALID
- Auth PASS: [726, 727]
- Auth FAIL: []
- Auth INDETERMINATE: []
- Decrypt failed: []
- Identity relation: SAME_IDENTITY
- Worker covered/pinned: False/False
- MANUAL_REVIEW_BLOCKER: LEGACY_MULTI_SESSION
- Runtime: MANUAL_REVIEW (LEGACY_MULTI_SESSION)
- Proposed authoritative: None
- OTP required: False
- Operator decision: True
- Canonicalization safety: REQUIRES_SESSION_RETIREMENT_DECISION
- Remediation: Same identity duplicates with no deterministic non-max(id) winner — operator must select authoritative session.
- Risk: MEDIUM

### Account 81
- Classification: **MULTIPLE_VALID_SAME_IDENTITY**
- Historical confirmed: True
- Sessions: [722, 723]
- Decrypt matrix: 722=DECRYPT_OK/STRUCTURALLY_VALID, 723=DECRYPT_OK/STRUCTURALLY_VALID
- Auth PASS: [722, 723]
- Auth FAIL: []
- Auth INDETERMINATE: []
- Decrypt failed: []
- Identity relation: SAME_IDENTITY
- Worker covered/pinned: False/False
- MANUAL_REVIEW_BLOCKER: LEGACY_MULTI_SESSION
- Runtime: MANUAL_REVIEW (LEGACY_MULTI_SESSION)
- Proposed authoritative: None
- OTP required: False
- Operator decision: True
- Canonicalization safety: REQUIRES_SESSION_RETIREMENT_DECISION
- Remediation: Same identity duplicates with no deterministic non-max(id) winner — operator must select authoritative session.
- Risk: MEDIUM

### Account 92
- Classification: **DECRYPT_REPAIR_REQUIRED**
- Historical confirmed: True
- Sessions: [11, 12]
- Decrypt matrix: 11=DECRYPT_FAILED/UNKNOWN, 12=DECRYPT_FAILED/UNKNOWN
- Auth PASS: []
- Auth FAIL: []
- Auth INDETERMINATE: []
- Decrypt failed: [11, 12]
- Identity relation: UNKNOWN
- Worker covered/pinned: False/False
- MANUAL_REVIEW_BLOCKER: LEGACY_MULTI_SESSION
- Runtime: MANUAL_REVIEW (LEGACY_MULTI_SESSION)
- Proposed authoritative: None
- OTP required: True
- Operator decision: False
- Canonicalization safety: REQUIRES_DECRYPTION_REPAIR
- Remediation: All Account92 sessions fail decryption with current SESSION_SECRET; no safe alternate-key repair proven. Relogin/OTP required after approval. Failure classes=['LEGACY_CIPHER_FORMAT']
- Risk: HIGH

## Recommended order
1. Account 2: one proven valid + invalid sibling (risk=LOW, otp=False, operator=False)
1. Account 12: same-identity duplicate needs operator session choice (risk=MEDIUM, otp=False, operator=True)
1. Account 19: same-identity duplicate needs operator session choice (risk=MEDIUM, otp=False, operator=True)
1. Account 81: same-identity duplicate needs operator session choice (risk=MEDIUM, otp=False, operator=True)
1. Account 92: decrypt/key repair investigation (risk=HIGH, otp=True, operator=False)

NEXT_SAFE_ACTION=Review the five forensic classifications and authorize the lowest-risk remediation account first.

