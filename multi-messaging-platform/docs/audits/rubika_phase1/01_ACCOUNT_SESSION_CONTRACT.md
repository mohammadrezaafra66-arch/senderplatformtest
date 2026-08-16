# Account & Session Contract

## Delivery modes

| Mode | Config | SessionType | Registration |
|------|--------|-------------|--------------|
| `bot_api` | `RUBIKA_DELIVERY_MODE=bot_api` | `API_TOKEN` | `POST /accounts/{id}/session/register` |
| `user_account` | `RUBIKA_DELIVERY_MODE=user_account` + `RUBIKA_USER_ACCOUNT_ENABLED=true` | `RUBIKA_SESSION` | OTP `.../rubika/session/register` + `verify` |

Invalid mode values raise at Settings load and in `required_session_type` / readiness (`CONFIG_INVALID`).

Central module: `core_engine.services.rubika_mode`.

## Bot API payload

- Plain non-empty token, or JSON with `bot_token` / `token` / `api_token`.
- Stored encrypted as `SessionType.API_TOKEN`.
- Rejected when current mode is `user_account`.

## User-account envelope (v1)

```json
{
  "version": 1,
  "phone_number": "...",
  "auth": "...",
  "guid": "...",
  "user_agent": "...",
  "private_key": "..."
}
```

Missing `version` ⇒ treat as v1. Unknown version rejected. Secrets never logged.

## ChannelSession current semantics

Append-only insert; **current session = max(id)** for `(account_id, session_type)`.
