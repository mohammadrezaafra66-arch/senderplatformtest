# Contacts 361–447 Bulk Soft Delete Result

**Committed:** 2026-09-02T12:33:19Z  
**Service:** `soft_delete_contact` (same as `POST /contacts/{id}/delete`)  
**Reason:** `bulk_operator_cleanup_361_447`  
**Actor:** `operator`

## Approved scope

| Field | Value |
|--------|--------|
| APPROVED_MIN_ID | 361 |
| APPROVED_MAX_ID | 447 |
| APPROVED_ID_COUNT | 87 |
| CONTACT448_SELECTED | False |

## Precheck

| Metric | Count |
|--------|------:|
| Existing contacts in approved set | 40 |
| Active before execution | 38 |
| Already soft-deleted before | 2 (446, 447) |
| Missing IDs (never existed) | 47 |

## Execution

| Metric | Count |
|--------|------:|
| Newly soft-deleted | 38 |
| Idempotent skips | 2 |
| Hard deletes | 0 |

## Post verification

| Check | Result |
|--------|--------|
| STILL_ACTIVE_APPROVED_IDS | `[]` |
| CONTACT448_PRESERVED | True |
| GLOBAL_ACTIVE before → after | 42 → 4 |
| ACTIVE_COUNT_RECONCILIATION_PASS | True (42 − 38 = 4) |
| OUTSIDE_APPROVED_SET_MUTATION_COUNT | 0 |
| DELETED_RANGE_HIDDEN_FROM_ACTIVE_DIRECTORY | True |
| DELETED_RANGE_HIDDEN_FROM_SEARCH | True |

## History preservation

| Reference | Before | After | Changed |
|-----------|-------:|------:|--------:|
| Campaign recipients | 0 | 0 | 0 |
| Messages | 0 | 0 | 0 |
| Attempts | 0 | 0 | 0 |
| Running campaign state mutations | — | — | 0 |

## Safety

- NO_CAMPAIGN_MUTATION=True
- NO_MESSAGE_MUTATION=True
- NO_ATTEMPT_MUTATION=True
- NO_SESSION_MUTATION=True
- NO_EXTERNAL_SEND=True
- EXTERNAL_SEND_ATTEMPTS=0
- MESSAGE_SENT=False

## Result

**BULK_CONTACT_SOFT_DELETE_PASS=True**

All 40 existing Contacts in the explicit approved ID set 361–447 are soft-deleted. Contact 448 was not selected or modified. No Contact outside the approved set was newly soft-deleted.
