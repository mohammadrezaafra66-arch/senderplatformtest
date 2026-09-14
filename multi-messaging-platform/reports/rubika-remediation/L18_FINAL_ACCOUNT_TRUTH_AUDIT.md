# L18 Final Account Truth Audit

Generated: 2026-09-01T08:35:59.357208+00:00

## Summary
- TOTAL_ACCOUNTS=48
- CLASSIFIED_ACCOUNTS=48
- UNEXPLAINED_ACCOUNTS=0
- Pre-deploy UI/backend label mismatches=43

## Grouped runtime statuses
- **LOGIN_REQUIRED** (37): [3, 4, 14, 15, 16, 17, 18, 20, 21, 22, 24, 25, 26, 28, 29, 67, 68, 69, 70, 71, 72, 73, 75, 76, 77, 78, 80, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91]
- **MANUAL_REVIEW** (5): [2, 12, 19, 81, 92]
- **READY** (5): [13, 23, 27, 74, 79]
- **SESSION_ERROR** (1): [1]

## Data path (forensic)

DB Account.status / ChannelSession / LoginChallenge / Redis coverage
→ `account_runtime_status.compute_*`
→ `GET /accounts` (`runtime`, `runtime_status`, `runtime_status_label`)
→ Accounts page connection column (not lifecycle `فعال` alone)

Legacy lie fixed: `status=active` is **وضعیت اکانت**, not **وضعیت اتصال**.

