# Failure Taxonomy

Categories: AUTH, SESSION, RATE_LIMIT, TRANSPORT, TIMEOUT, RECIPIENT, CONFIG,
DEPENDENCY_REDIS, DEPENDENCY_DB, POLICY, UNKNOWN

Severity: INFO, WARNING, CRITICAL

Classifier: `classify_rubika_failure(code, ...)` → `RubikaFailureEvent`

Account-guilt categories exclude DEPENDENCY_*, POLICY, CONFIG, RECIPIENT.
Session/auth categories may escalate to quarantine + REQUIRES_LOGIN.
