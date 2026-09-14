# E2E Campaign Architecture Map

Generated: 2026-09-01

## End-to-end flow

```
ACCOUNT (accounts.id)
  → AUTH / SESSION (channel_sessions, rubika_login_state_machine)
  → L18 RUNTIME (account_runtime_status.compute_*)
  → C1 ELIGIBILITY (campaign_sender_eligibility.evaluate_*)
  → CONTACT (contacts.id, phone normalized)
  → CAMPAIGN (campaigns.id, status state machine)
  → CAMPAIGN_RECIPIENT (campaign_recipients)
  → PREPARE (phase4_prepare.prepare_campaign_messages)
       → MESSAGE (messages.id, account_id round-robin)
       → RENDERED_MESSAGE
       → STAGED_QUEUE_ITEM (ready)
  → PREFLIGHT (campaign_preflight.evaluate_campaign_send_preflight)
  → START (campaign_control.start_campaign)
  → QUEUE BRIDGE (queue_bridge.push_staged_items_to_worker_queue → Redis)
  → WORKER (base_worker.run_once → RubikaWorker)
  → ADAPTER (deliver_rubika_live / fake in tests)
  → MessageAttempt + CampaignRecipient.send_status
```

## Stage reference

| Stage | Source | DB | API | Frontend |
|-------|--------|-----|-----|----------|
| Account create | `api/accounts.py` | `accounts` | POST /accounts | accounts.tsx |
| Rubika login | `rubika_login_state_machine.py` | `rubika_login_challenges`, `channel_sessions` | POST .../login | ApiTokenSessionPanel |
| L18 runtime | `account_runtime_status.py` | read-only | GET /accounts | accounts.tsx |
| C1 eligibility | `campaign_sender_eligibility.py` | — | GET /accounts, preflight | CampaignSenderSelector |
| Contact import | `api/contacts.py` | `contacts`, `import_batches` | POST /imports | contacts.tsx |
| Campaign create | `api/campaigns.py` | `campaigns`, `campaign_recipients` | POST /campaigns/from-* | create.tsx |
| Sender assign | `api/campaigns.py` `_sync_campaign_accounts` | `campaign_accounts` | PUT /campaigns/{id}/accounts | [id].tsx |
| Prepare | `phase4_prepare.py` | messages, staged_queue_items | debug prepare / auto on start | — |
| Preflight | `campaign_preflight.py` | read-only | GET /campaigns/{id}/preflight | [id].tsx |
| Start | `campaign_control.py` | campaigns.status | POST /campaigns/{id}/start | [id].tsx |
| Queue | `queue_bridge.py` | staged_queue_items | — | — |
| Worker | `workers/base_worker.py` | — | — | — |
| Attempt | `workers/db.py` | message_attempts | — | message logs |

## Campaign state machine (actual)

| Status | Meaning |
|--------|---------|
| draft | Created, not prepared |
| prepared | Messages staged |
| running | Active dispatch |
| paused | Operator stop |
| completed / failed / cancelled | Terminal |

**Separate dimensions (must not collapse):**
- `campaign_prepared` — staged messages exist
- `audience_ready` — valid recipients
- `sender_assignment_ready` — CampaignAccount links or auto pool
- `runtime_accounts_ready` — C1 campaign_eligible count > 0
- `capacity_ready` — preflight capacity aggregate
- `allowed_to_start` — preflight gate composite

## Single source of truth target

| Concern | Authoritative module |
|---------|---------------------|
| Account runtime | L18 `account_runtime_status` |
| Campaign sender eligibility | C1 `campaign_sender_eligibility` |
| Sender candidate audit | `campaign_readiness_contract` |
| Preflight send gate | `campaign_preflight` + `rubika_preflight` |
| Auto selection | `filter_auto_select_eligible` (C1) |
| Manual assignment | Allowed non-ready; warnings returned |
| Start block | preflight + `start_blocker_for_assigned_senders` |
| Worker session | `load_rubika_runtime_session` (canonical fail-closed) |

## Root cause: prior UI drift

Deployed frontend used lifecycle `account.status` (`فعال`) in sender picker while backend/preflight used L18. **Not a backend data missing issue** — frontend bundle staleness + dual render paths.
