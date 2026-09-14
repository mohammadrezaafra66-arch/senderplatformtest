# L16 — Replacement OTP Pilot Selection

**CURRENT_PHASE=L16_REPLACEMENT_OTP_PILOT_SELECTION**  
**PHASE_STATUS=READY_FOR_OPERATOR_SELECTION**

## Source audit (`scripts/_l16_park_and_inventory.py`)

| Gate | Result |
|------|--------|
| PARK_SCRIPT_SOURCE_AUDIT_PASS | **True** |
| ACCOUNT3_PARK_ACTION | leave challenge intact until natural expiry; clear ephemeral Redis handshake only |
| ACCOUNT3_CHALLENGE_DELETE_PRESENT | **False** |
| ACCOUNT3_SECOND_OTP_REQUEST_PRESENT | **False** |
| INVENTORY_DB_READ_ONLY | **True** (`SET TRANSACTION READ ONLY` + rollback) |
| INVENTORY_REDIS_READ_ONLY | **True** (inventory uses `llen`/`ping` only) |
| SCRIPT_SECRET_SAFE | **True** (masked phones only) |

## Account3 park

| Field | Value |
|-------|-------|
| ACCOUNT3_PARKED_SAFELY | **True** |
| ACCOUNT3_CHALLENGE_STATE | `otp_waiting_for_operator` |
| ACCOUNT3_SESSION_COUNT | **0** |
| ACCOUNT3_CHALLENGE_EXPIRES_IN_SECONDS | **-421** (already past TTL; row preserved; L3 will mark `login_expired` on next Account3-scoped request) |
| Challenge deleted | False |
| Second OTP requested | False |
| OTP submitted | False |

No L3 `CANCELLED` state exists — preferred policy applied: leave intact until natural expiry.

## Other-account login independence

```
OTHER_ACCOUNT_LOGIN_ALLOWED=True
```

Evidence: `_get_active_challenge(db, account_id)` is account-scoped only; Account3 pending does not block other accounts.

## No-session inventory

```
NO_SESSION_ACCOUNT_IDS=[3,14,15,16,17,18,20,21,22,24,25,26,27,28,29,67,69,70,72,73,76,77,78,80,82,83,84,85,86,87,88,89,91]
```

Excluded from next pilot: 1,2,3,12,13,19,23,74,79,81,92 (+ Account3 parked / no phone access).

## SAFE_NEXT_PILOT_CANDIDATES

| account_id | masked_phone | readiness |
|---:|---|---|
| 14 | 989***558 | none |
| 15 | 989***557 | none |
| 16 | 989***553 | none |
| 17 | 989***552 | none |
| 18 | 989***551 | none |
| 20 | 989***549 | none |
| 21 | 989***544 | none |
| 22 | 989***545 | none |
| 24 | 989***547 | none |
| 25 | 989***548 | none |
| 26 | 989***531 | none |
| 27 | 989***530 | none |
| 28 | 989***529 | none |
| 29 | 989***528 | none |
| 67 | 989***905 | none |
| 69 | 989***541 | none |
| 70 | 989***902 | none |
| 72 | 989***539 | none |
| 73 | 989***542 | none |
| 76 | 989***538 | none |
| 77 | 989***904 | none |
| 78 | 989***870 | none |
| 80 | 989***540 | none |
| 82 | 989***533 | none |
| 83 | 989***534 | none |
| 84 | 989***535 | none |
| 85 | 989***536 | none |
| 86 | 989***532 | none |
| 87 | 989***526 | none |
| 88 | 989***525 | none |
| 89 | 989***523 | none |
| 91 | 989***927 | none |

## Production topology (unchanged)

```
CURRENT_ACTIVE_WORKER_IDS=[12,13,23,74,79]
GLOBAL_ACTIVE_SESSION_COUNT=3
OTP_REQUESTED_FOR_OTHER_ACCOUNT=False
MESSAGE_SENT=False
```

## Next

```
EXACT_OPERATOR_INPUT_REQUIRED=Choose one candidate whose Rubika phone is accessible now
```

Do **not** request OTP until the operator selects an accessible account.
