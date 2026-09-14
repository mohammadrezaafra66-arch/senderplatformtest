# C1 — Campaign Sender Truth Audit

Generated during M2+C1 autonomous remediation.

## B1 Forensic evidence (pre-change)

- Campaign picker rendered `platform · account_status_*` → e.g. `rubika · فعال` from lifecycle `Account.status`, not L18 runtime.
- `compatibleActiveAccounts` filtered only `status === "active"`.
- L18 fields existed on `GET /accounts` but were unused by Campaign UI.
- Preflight readiness `ready/assigned` (e.g. `1/1`) could be misread as identity; `accountDisplayName` could show numeric `label`.
- Rubika Start already hard-gated via `evaluate_campaign_send_preflight`; non-Rubika remained weaker.

## C1 changes

1. New authoritative `evaluate_campaign_sender_eligibility` consuming L18 runtime.
2. Auto sender resolve filters `campaign_eligible=True` only.
3. Manual save still allows ACTIVE assignment (`ASSIGNED_BUT_NOT_READY`); picker disables non-ready checkboxes.
4. Accounts + campaign sender APIs expose `display_identity`, runtime, eligibility, blockers.
5. Frontend picker uses campaign eligibility + Persian readiness labels; filters همه / آماده ارسال / نیازمند اقدام.
6. Display identity never uses index/count/`1/1`.

## Safety

- No OTP, no message send/enqueue, no session mutation, no L17 config change, no DB migration in C1 deploy.
- Deploy consumers: `mmp_core_api` restart + `frontend` rebuild `--no-deps` only.

## M2 prerequisite

Account2 remediation COMPLETE: Session721 ACTIVE, Session2 `decrypt_failed`, runtime READY.

## Post-recovery status (2026-09-01)

- Docker engine recovered; C1 frontend deployed (`senderFilterReady` in live bundle).
- API verification: READY accounts 2/13/23/27/74/79; MANUAL_REVIEW 12/19/81/92; SESSION_ERROR 1; LOGIN_REQUIRED for no-session accounts.
- Preflight batch-runtime parity patch applied (campaign detail ↔ preflight consistency).
- See `C1_CAMPAIGN_PRODUCTION_RECONCILIATION.md`.
