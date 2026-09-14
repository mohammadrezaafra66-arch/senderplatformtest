# L16 — Account27 OTP_INVALID Recovery Audit

**CURRENT_PHASE=L16_ACCOUNT27_OTP_INVALID_RECOVERY**  
**PHASE_STATUS=READY_FOR_FRESH_OTP**  
**OTP_REQUESTED_AGAIN=False**

## Forensic (read-only)

| Field | Value |
|-------|-------|
| ACCOUNT27_SESSION_COUNT | **0** |
| ACCOUNT27_ACTIVE_SESSION_COUNT | **0** |
| ACCOUNT27_VALIDATING_SESSION_COUNT | **0** |
| ACCOUNT27_LOGIN_CHALLENGE_COUNT | **1** |
| ACCOUNT27_ACTIVE_LOGIN_CHALLENGE_COUNT | **0** |
| LATEST_CHALLENGE_ID | `a199bdc4e4024f8380a6dba68be1b079` |
| LATEST_CHALLENGE_STATE | `login_failed` |
| LATEST_CHALLENGE_FAILURE_CODE | `OTP_INVALID` |
| LATEST_CHALLENGE_ATTEMPT_COUNT | 1 |
| LATEST_CHALLENGE_EXPIRED | True (TTL elapsed) |
| LATEST_CHALLENGE_RETRY_ALLOWED | **False** |
| candidate_session_id | null |
| completed_session_id | null |
| GLOBAL_ACTIVE_SESSION_COUNT | **3** |
| MessageAttempt_count | **5** (unchanged) |

## Partial-login check

```
OTP_INVALID_LEFT_PARTIAL_SESSION=False
OTP_INVALID_LEFT_PARTIAL_IDENTITY=False
OTP_INVALID_LEFT_PARTIAL_WORKER=False
```

No ACTIVE/VALIDATING session; no GUID bind; no worker27 / not in cohort / not in ENFORCE allowlist.

## Failure classification (L3 semantics)

```
OTP_FAILURE_CLASSIFICATION=CHALLENGE_TERMINAL_AFTER_INVALID
CURRENT_CHALLENGE_REUSABLE=False
```

Evidence: on provider `OTP_INVALID`, L3 sets challenge to `LOGIN_FAILED`.  
`login_failed` is **not** in `ACTIVE_CHALLENGE_STATES`, so submit cannot continue on this challenge (`CHALLENGE_NOT_ACTIVE`). Forensic row is preserved.

## Fresh OTP request safety (not executed)

```
SAFE_TO_REQUEST_FRESH_ACCOUNT27_OTP=True
```

`request_rubika_login(27)` will ignore the terminal `login_failed` row, create a **new** challenge after cooldown, and leave the failed challenge as history.

## Local OTP entry hardening

```
LOCAL_OTP_ENTRY_FLOW_READY=True
```

`scripts/_l16_otp_account.py submit`:
- refuses non-TTY (`NO_TTY`)
- uses `getpass` (no echo)
- rejects OTP via argv/env
- never writes OTP to reports

**Required invocation (after fresh OTP is approved):**

```powershell
docker exec -it -e PYTHONPATH=/app -w /app mmp_core_api python scripts/_l16_otp_account.py submit 27
```

## Stop

Do **not** request a second OTP until operator approves.

```
EXACT_OPERATOR_INPUT_REQUIRED=Approve exactly one fresh OTP request for Account27
```
