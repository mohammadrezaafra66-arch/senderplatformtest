# MANUAL_LIVE_TEST

These scripts call **live** OpenAI or AfraKala APIs. They are **not** pytest tests.

They refuse to run unless:

```
MANUAL_LIVE_TEST=1
PILOT_CONFIRM=SEND
```

CI never sets these flags. Do not import these modules from automated tests except to assert the guard refuses.

`smoke_openai.py` — one synthetic variation request, no PII, no Rubika send.
`smoke_afrakala.py` — one read-only product fetch; prints counts only.

Live Rubika send is **not** provided here. After owner approval, the production path is campaign → queue → worker, with kill switch `REAL_MESSAGE_SENDING_ENABLED`.
