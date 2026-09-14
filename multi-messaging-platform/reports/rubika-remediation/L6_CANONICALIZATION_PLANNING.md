# L6 Canonicalization Planning (no promotion)

**Status:** COMPLETE (planning only)  
**Production mutations:** none  
**Flag:** `RUBIKA_CANONICAL_SESSION_V1` remains **false**

## Wrapper fix (post-L3)

`scripts/_l5_production_schema_apply.py`:

- `verify_l2()` expects `rubika_l2_canonical_session_001`
- `verify_l3()` / `verify_post()` expect `rubika_l3_login_challenge_001`
- Shared session invariants (counts, ACTIVE=0, Account12/79 mappings, duplicates) live in `_assert_post_migration_session_invariants` and do **not** re-assert L2 alembic after L3

Regression: `tests/core_engine/test_l5_post_l3_version_check.py`

## Inventory summary

| Group | Account IDs |
|---|---|
| SINGLE_PROVEN_SESSION | 13, 23, 74, 79 |
| MULTIPLE_PROVEN_SESSIONS | 12 (dual-era send evidence) |
| DECRYPT_FAILED | 1, 92 |
| MANUAL_REVIEW_REQUIRED | 2, 12, 19, 81, 92 |
| NO_USABLE_SESSION (among session-row holders) | — |

## Account12 / Account79 (protected)

| Account | Recommended candidate | Ambiguous | Notes |
|---|---|---|---|
| 12 | **728** (current runtime) | **True** | SUCCESS attempt 2 (2026-08-26) pre-dates 728 → implicates **657**; attempt 15 used 728. Do not promote. |
| 79 | **600** | **False** | Sole session; reconnect/identity/send OK. Still pinned — no auto-promote. |

## Duplicate analysis (all MANUAL_REVIEW)

| Acct | Candidate | Others | Action |
|---|---|---|---|
| 2 | 721 | 2 | MANUAL_REVIEW (secondary UNPROBED) |
| 12 | 728 | 657 | MANUAL_REVIEW + protection |
| 19 | 727 | 726 | MANUAL_REVIEW |
| 81 | 723 | 722 | MANUAL_REVIEW |
| 92 | none | 11,12 | MANUAL_REVIEW (both decrypt fail) |

## Batches

- **A:** 13, 23, 74 (79 gated separately)
- **B:** empty until secondary probes remove ambiguity
- **C:** 1, 2, 12, 19, 81, 92

## Feature flag

Do **not** flip global `RUBIKA_CANONICAL_SESSION_V1` after first promotion. Propose allowlist + `off|shadow|enforce` mode before any runtime switch. See JSON plan for full prechecks/rollback.

## Next safe action

Operator review of exact canonical candidate plan before any session promotion.
