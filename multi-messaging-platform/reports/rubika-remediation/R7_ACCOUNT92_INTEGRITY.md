# R7 — Account92 / session cardinality investigation

**Mode:** read-only (no deletes, no constraints applied yet)  
**Date:** 2026-08-29

## Account 92

| Field | Value |
|-------|-------|
| exists | true |
| platform | rubika |
| status | active |
| ChannelSession rows | **2** (ids 11, 12) both `rubika_session` |
| RubikaAccountPool rows | **2** — phases `day`, `pilot-1` (unique pairs; not duplicates) |

Latest session id=12 wins for readiness (existing `_latest_session_row` behavior).

## Fleet signals

Accounts with multiple ChannelSession rows (sample): 2, 12, 19, 81, 92 (each 2 rows).

Duplicate `(account_id, phase)` pool pairs: **none** (unique constraint holds).

## Intended cardinality (provisional)

| Entity | Intended | Current |
|--------|----------|---------|
| RubikaAccountPool | unique `(account_id, phase)` | Enforced |
| ChannelSession | ideally ≤1 active row per `(account_id, session_type)` | **Not enforced**; historical duplicates exist |

## Recommendation

Do **not** delete Account92 rows blindly. Before any migration:

1. Confirm product rule: one ciphertext session per `(account_id, session_type)`.
2. Mark historical rows vs active (add `superseded_at` / soft flag) rather than hard delete.
3. Only then add a partial unique index on active sessions.

Evidence: `R7_ACCOUNT92_INTEGRITY.json`
