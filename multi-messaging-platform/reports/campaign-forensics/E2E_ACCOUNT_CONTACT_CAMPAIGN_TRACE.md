# E2E Account → Contact → Campaign Trace

## Account onboarding (Rubika)

1. **Create** — `POST /accounts` → `accounts` row (status=active)
2. **Login** — L3 `request_rubika_login` / `submit_rubika_login_code` → `channel_sessions`
3. **Promotion** — canonical session lifecycle (ACTIVE/SUPERSEDED) via rubika_canonical_session
4. **Pool** — `rubika_pool` enrollment via L17 automation
5. **Worker** — dynamic discovery + Redis coverage keys
6. **L18** — `compute_rubika_runtime_status` → READY | MANUAL_REVIEW | ...
7. **C1** — `evaluate_campaign_sender_eligibility` → campaign_eligible

Synthetic zero-session account: use fake login provider in tests (`rubika_login_fake_provider`).

## Contact path

1. **Manual** — contacts API
2. **Import** — Excel commit → `import_batches`, `contacts`
3. **Normalization** — phone validation in import pipeline
4. **Dedup** — import duplicate detection + campaign recipient dedupe_key

## Campaign audience

1. **Input** — contact_ids or import_batch_id
2. **Materialization** — `CampaignRecipient` rows at create
3. **Consent/block** — filtered at create with reason_code on skip
4. **Prepare** — one Message per eligible recipient; `dedupe_key=campaign:{id}:contact:{id}`

## Sender selection

| Mode | Resolver | Eligibility |
|------|----------|-------------|
| Auto | `resolve_campaign_sender_accounts` empty links | `filter_auto_select_eligible` (C1 only) |
| Manual | CampaignAccount links | ACTIVE lifecycle; ASSIGNED_BUT_NOT_READY allowed |

## Start → dispatch (no real send in forensics)

1. `_auto_prepare` if needed
2. `evaluate_campaign_send_preflight` (Rubika)
3. status=RUNNING
4. `push_staged_items_to_worker_queue`
5. Worker `deliver_*` with fake provider in tests
6. `MessageAttempt` created on result

## ASSIGNED ≠ READY

Assignment persists in `campaign_accounts`. Readiness is recomputed on every preflight/accounts fetch via L18+C1. Session expiry or worker loss updates blockers without removing assignment.
