# M3 — Duplicate Resolution Plan (read-only recommendations)

Generated: 2026-09-01T11:35:32.240823+00:00

## Summary

No deterministic winner found for any target account under M3 strong-evidence rules.
Accounts 19 and 81 are operationally equivalent pairs suitable for a single neutral
duplicate-collapse policy after operator approval. Account 12 has conflicting
time-windowed dispatch evidence on both sessions and requires a separate decision.

## Per-account

### Account 12 (sessions [657, 728])
- **NO_OBJECTIVE_WINNER** — operator decision required.
- STRONG_DIFFERENTIATORS: ['B_CONFLICTING_DISPATCH_657_AND_728']
- Expected post-canonical: MANUAL_REVIEW
- Worker state: PINNED_COVERAGE
- Campaign eligibility after selection: False

### Account 19 (sessions [726, 727])
- **NO_OBJECTIVE_WINNER** — operator decision required.
- STRONG_DIFFERENTIATORS: []
- Expected post-canonical: MANUAL_REVIEW
- Worker state: ABSENT
- Campaign eligibility after selection: False

### Account 81 (sessions [722, 723])
- **NO_OBJECTIVE_WINNER** — operator decision required.
- STRONG_DIFFERENTIATORS: []
- Expected post-canonical: MANUAL_REVIEW
- Worker state: ABSENT
- Campaign eligibility after selection: False

## Neutral duplicate-collapse policy (not executed in M3)

NEUTRAL_DUPLICATE_COLLAPSE_POLICY_POSSIBLE=True

Operator-approved neutral duplicate-collapse for accounts 19 and 81: both pairs are operationally equivalent (AUTH_PASS, same GUID, no dispatch/runtime differentiator). Use one documented neutral tie-break (e.g. lowest session_id) with non-winner marked SUPERSEDED — never max(id) or updated_at alone. Account 12 requires separate operator decision: both sessions have time-windowed SUCCESS dispatch evidence (657→attempt 2 pre-728; 728→attempt 15 post-create); do not auto-select 728 via max-id or recency.

## Recommended order

RECOMMENDED_REMEDIATION_ORDER=[19, 81, 12]

NEXT_SAFE_ACTION: Obtain operator approval for neutral collapse on 19/81; resolve Account 12 separately.

