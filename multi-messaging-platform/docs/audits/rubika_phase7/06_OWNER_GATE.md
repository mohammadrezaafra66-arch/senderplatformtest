# 06 OWNER GATE

LIVE PILOT READY: **NO** (environment/account/recipient not owner-identified)

ACCOUNT: none selected (no owner-safe account in this pod)

RECIPIENT: not provided

MESSAGE: proposed Pilot 1 template (not sent):

`سلام {{first_name}}، این یک پیام آزمایشی داخلی است.`

GPT: OFF

PRODUCTS: OFF

CIRCUIT: CLOSED (this Redis)

ACCOUNT HEALTH: n/a (no selected account)

SESSION: n/a

CAMPAIGN PREFLIGHT: service ready; not run against an owner campaign

CAPACITY: planner ready

KILL SWITCH: verified OFF → transport_not_called

ROLLBACK: documented in `08_ROLLBACK_PLAN.md`

---

Owner approval requested for a later step (not executed here):

**Enable real Rubika transport for exactly 1 message to the approved pilot recipient.**

Required from owner before PILOT_LIVE:

1. Identify one Rubika account (id + label) that is safe for pilot
2. Identify one consented test recipient (masked)
3. Confirm exact final_text after freeze/preview
4. Explicitly approve: `PILOT_CONFIRM=SEND` with `REAL_MESSAGE_SENDING_ENABLED=true` for that single send
5. Confirm one `rubika_worker` replica only

approval_received: **NO**
approved_recipient: none
approved_message_count: 0
approved_features: none
