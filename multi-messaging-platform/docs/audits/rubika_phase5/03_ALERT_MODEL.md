# Alert Model

## Types
ACCOUNT_DEGRADED, ACCOUNT_QUARANTINED, SESSION_REQUIRES_LOGIN, CIRCUIT_OPEN, CIRCUIT_HALF_OPEN, SYSTEM_DEPENDENCY_FAILURE, HIGH_FAILURE_RATE, ACCOUNT_RECOVERED

## Severity
INFO | WARNING | CRITICAL

## Storage
Redis keys `rubika:alert:{dedupe}` + open index. No migration.

## Dedupe
`type + account|system + incident|none + category`  
Unresolved alerts bump `occurrence_count` / `last_seen`.

## Delivery
`AlertSink` protocol; default `LoggingAlertSink`. No external credentials.

## Redaction
`redact_secrets()` strips token/password/otp/private_key/ciphertext fields and patterns.
