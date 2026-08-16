# Readiness Matrix

Function: `evaluate_account_session_readiness` → `SessionReadiness(ready, code, message, error, session_type, delivery_mode)`.

| Condition | code | ready |
|-----------|------|-------|
| Account None | ACCOUNT_MISSING | false |
| Invalid RUBIKA_DELIVERY_MODE | CONFIG_INVALID | false |
| user_account && !ENABLED | USER_ACCOUNT_DISABLED | false |
| BANNED | ACCOUNT_BANNED | false |
| REQUIRES_LOGIN | ACCOUNT_REQUIRES_LOGIN | false |
| RESTING | ACCOUNT_DISABLED | false |
| Missing identifier | ACCOUNT_IDENTIFIER_MISSING | false |
| No encrypted session | SESSION_MISSING | false |
| Decrypt fail | SESSION_DECRYPT_FAILED | false |
| Payload/envelope invalid | SESSION_INVALID | false |
| Valid ACTIVE + session | READY | true |

API `error` field = lowercase `code` for backward compatibility.
`build_account_session_status` exposes Rubika `delivery_mode`.
