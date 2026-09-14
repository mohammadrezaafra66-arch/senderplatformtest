# L7 — Canonical Cohort Gating + Duplicate Session Probes

**Phase status:** COMPLETE  
**Generated:** see `L7_DUPLICATE_SESSION_PROBES.json`  
**Mutations:** none (no ACTIVE, no OTP, no send, no Redis/DB session writes)

## Runtime modes implemented

| Setting | Values | Default |
|---|---|---|
| `RUBIKA_CANONICAL_SESSION_MODE` | `off` \| `shadow` \| `enforce` | `off` |
| `RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS` | comma-separated cohort | empty |

Semantics:

- **off** — legacy max(id) authoritative; canonical unused at runtime.
- **shadow** — legacy authoritative; allowlisted accounts get read-only compare (`SHADOW_*` metrics). No DB/Redis mutation; selection unchanged.
- **enforce** — canonical ACTIVE loader authoritative **only** for allowlisted accounts; fail-closed (`NO_ACTIVE_SESSION`, `MULTIPLE_ACTIVE_SESSIONS`, decrypt/identity errors). No max(id) fallback. Empty/malformed allowlist → nobody enforced.

`RUBIKA_CANONICAL_SESSION_V1` remains for login/prover fencing; default `false` keeps prior behavior. Global enforce is **not** the first rollout path.

Code: `core_engine/services/rubika_canonical_runtime.py`, wired via `workers/session_access.py`.

## Isolated tests

`tests/core_engine/test_rubika_l7_canonical_cohort.py` — **17 passed** on `mmp_test_isolation`.

## Duplicate per-session_id probes (production read-only)

| Account | Classification | Notes |
|---|---|---|
| 2 | `ONE_PROVEN_ONE_INVALID` | 721 PASS; session 2 decrypt FAIL → Batch B candidate **721** |
| 12 | `BOTH_VALID_CURRENT_IDENTITY` | 657+728 both reconnect, same GUID → **manual review**, not safe to promote |
| 19 | `BOTH_VALID_SAME_IDENTITY` | 726+727 both valid same identity → manual |
| 81 | `BOTH_VALID_SAME_IDENTITY` | 722+723 both valid same identity → manual |
| 92 | `DECRYPT_REPAIR_OR_RELOGIN_REQUIRED` | 11+12 decrypt fail; do not delete |

### Account12 forensic

- Both decrypt OK, structure OK, reconnect PASS, identity match **same GUID**
- Current legacy-selected: **728**
- Historical real-send: attempt 2 pre-728 (657 era); attempt 15 post-728
- Older 657 is **still currently valid**
- Classification: `BOTH_VALID_CURRENT_IDENTITY`
- Recommended candidate (informational only): **728**
- `ACCOUNT12_SAFE_TO_PROMOTE=False`

### Account2 / 19 / 81

- Only account **2** has exactly one valid identity-matching candidate (721) with the other invalid → eligible for later Batch B
- 19 / 81 both dual-valid → remain manual (do not invent a winner)

## Batch A re-verify

| Account | Candidate session | Verified |
|---|---|---|
| 13 | **729** | True |
| 23 | **725** | True |
| 74 | **724** | True |

## Account79 protected

- Session **600**: decrypt/structure/reconnect/identity PASS
- Remains separately protected — not promoted

## Batch plan (no promotion)

- `BATCH_A_FINAL` = 13, 23, 74
- `BATCH_B_FINAL` = 2 (candidate 721 only after operator approval)
- `MANUAL_REVIEW_FINAL` = 1, 12, 19, 81, 92
- Account79 gated separately (valid, protected)

## Next safe action

Review L7 before first controlled canonical session promotion (Batch A → ACTIVE with mode still `off`, then shadow cohort, then enforce allowlist).
