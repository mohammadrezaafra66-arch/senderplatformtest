# L5 — PRODUCTION L3 SCHEMA APPLY RESULT

**Date:** 2026-08-31  
**Stage:** L3 ONLY  
**Canonical flag enabled:** NO  
**OTP / send / worker restart / core_api restart:** NO

## Backup used

- Path: `reports/rubika-remediation/l5_production_apply/backups/20260831T072323Z/mmp_db_pre_l5_20260831T072323Z.dump`
- SHA256: `ced57addabdd0b46a8f3e91d65d2e4fd5c0f5f18a1bf4e0a11a91ec5f887a591`
- Freshness at apply: ~3 minutes (within 30m window)

## Pre-L3

Wrapper re-ran `verify_l2` + runtime gates, then applied L3. Pre-L3 gates passed (Alembic then ran).

## Post-L3 (authoritative production queries)

| Check | Result |
|---|---|
| alembic_version | `rubika_l3_login_challenge_001` |
| rubika_login_challenges | exists |
| challenge rows | **0** |
| rubikaloginchallengestate | **1** type |
| channel_sessions | 15 |
| legacy_unclassified | 15 |
| ACTIVE | 0 |
| Account12 | 657, 728 |
| Account79 | 600 |
| ownership | 600→79, 657→12, 728→12 |
| duplicates | 2,12,19,81,92 |
| Rubika accounts | 43 |
| Campaigns 101/102 | paused |

## Wrapper note

`L5_APPLY_STEP=l3` exited code 2 because `verify_post()` incorrectly re-calls `verify_l2()`, which asserts `alembic_version == rubika_l2_canonical_session_001` after L3 has already advanced the version. **Schema apply succeeded**; post-check logic needs a follow-up fix before relying on automated `verify_post` alone. Manual read-only verification above is authoritative for this apply.

## Runtime enablement

Not performed. No core_api restart. Operator must approve any restart separately.
