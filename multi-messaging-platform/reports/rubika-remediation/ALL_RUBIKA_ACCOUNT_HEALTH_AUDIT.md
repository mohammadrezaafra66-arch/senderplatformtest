# ALL Rubika Account Health Audit

Audited at: `2026-08-29T12:19:04.352885+00:00`

**TOTAL_RUBIKA_ACCOUNTS=43**

Account IDs: 1, 2, 3, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 67, 69, 70, 72, 73, 74, 76, 77, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92

| Account ID | Account | Session | Reconnect | Identity | Pool | Schedule | Coverage | Preflight | Real Send | CURRENT STATE | Next Action |
|---:|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | SESSION_PRESENT_UNVERIFIED | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | SESSION_DECRYPT_FAILED | session_decrypt_repair_or_relogin |
| 2 | rubika-2 | MULTIPLE_SESSION_ROWS | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED | review_duplicate_sessions_no_auto_delete |
| 3 | rubika-3 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 12 | rubika-12 | MULTIPLE_SESSION_ROWS | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | True | True/READY | 2 | VERIFIED_REAL_SEND_OK | keep_as_proven_sender_review_duplicate_s |
| 13 | rubika-13 | SESSION_PRESENT_UNVERIFIED | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | AUTHENTICATED_NOT_DISPATCH_READY | fix_dispatch_gates_not_relogin |
| 14 | rubika-14 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 15 | rubika-15 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 16 | rubika-16 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 17 | rubika-17 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 18 | rubika-18 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 19 | rubika-19 | MULTIPLE_SESSION_ROWS | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED | review_duplicate_sessions_no_auto_delete |
| 20 | rubika-20 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 21 | rubika-21 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 22 | rubika-22 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 23 | rubika-23 | SESSION_PRESENT_UNVERIFIED | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | AUTHENTICATED_NOT_DISPATCH_READY | fix_dispatch_gates_not_relogin |
| 24 | rubika-24 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 25 | rubika-25 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 26 | rubika-26 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 27 | rubika-27 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 28 | rubika-28 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 29 | rubika-29 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 67 | rubika-67 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 69 | rubika-69 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 70 | rubika-70 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 72 | rubika-72 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 73 | rubika-73 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 74 | rubika-74 | SESSION_PRESENT_UNVERIFIED | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | AUTHENTICATED_NOT_DISPATCH_READY | fix_dispatch_gates_not_relogin |
| 76 | rubika-76 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 77 | rubika-77 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 78 | rubika-78 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 79 | rubika-79 | SESSION_PRESENT_UNVERIFIED | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | True | True/READY | 1 | VERIFIED_REAL_SEND_OK | keep_as_proven_sender |
| 80 | rubika-80 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 81 | rubika-81 | MULTIPLE_SESSION_ROWS | AUTH_RECONNECT_PASS | True | IN_POOL | APPLICABLE | False | True/READY | 0 | MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED | review_duplicate_sessions_no_auto_delete |
| 82 | rubika-82 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 83 | rubika-83 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 84 | rubika-84 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 85 | rubika-85 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 86 | rubika-86 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 87 | rubika-87 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 88 | rubika-88 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 89 | rubika-89 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 91 | rubika-91 | NO_SESSION_ROW | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | NO_SESSION_ROW | operator_initial_login |
| 92 | rubika-92 | MULTIPLE_SESSION_ROWS | SKIPPED | None | IN_POOL | APPLICABLE | False | None/None | 0 | MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED | session_decrypt_repair_or_relogin |

## Totals

- **TOTAL_RUBIKA_ACCOUNTS** = `43`
- **VERIFIED_REAL_SEND_OK** = `2`
- **READY_AUTHENTICATED** = `0`
- **AUTHENTICATED_NOT_DISPATCH_READY** = `3`
- **LOGIN_REQUIRED** = `0`
- **NO_SESSION_ROW** = `33`
- **SESSION_DECRYPT_FAILED** = `1`
- **SESSION_STRUCTURALLY_INVALID** = `0`
- **IDENTITY_MISMATCH** = `0`
- **MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED** = `4`
- **OTHER_BLOCKER** = `0`
- **RECOVERED_SINCE_PREVIOUS_AUDIT_COUNT** = `6`
- **RECOVERED_ACCOUNT_IDS** = `[2, 13, 19, 23, 74, 81]`

OTP_LOGIN_REQUIRED_ACCOUNT_IDS=[3, 14, 15, 16, 17, 18, 20, 21, 22, 24, 25, 26, 27, 28, 29, 67, 69, 70, 72, 73, 76, 77, 78, 80, 82, 83, 84, 85, 86, 87, 88, 89, 91]

## Forensic anomalies (read-only; no mutation)

- multiple_session_rows: `[{'account_id': 2, 'session_ids': [721, 2], 'session_count': 2, 'current_primary_state': 'MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED'}, {'account_id': 12, 'session_ids': [728, 657], 'session_count': 2, 'current_primary_state': 'VERIFIED_REAL_SEND_OK'}, {'account_id': 19, 'session_ids': [727, 726], 'session_count': 2, 'current_primary_state': 'MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED'}, {'account_id': 81, 'session_ids': [723, 722], 'session_count': 2, 'current_primary_state': 'MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED'}, {'account_id': 92, 'session_ids': [12, 11], 'session_count': 2, 'current_primary_state': 'MULTIPLE_SESSION_ROWS_REVIEW_REQUIRED'}]`
- identity_mismatch: `[]`
- nonempty_queues: `[]`

REAL_MESSAGES_SENT_DURING_AUDIT=False
OTP_REQUESTED_DURING_AUDIT=False
PRODUCTION_DATA_MUTATED=False
