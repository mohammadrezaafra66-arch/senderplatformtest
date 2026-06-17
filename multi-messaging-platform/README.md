# Multi Messaging Platform

## Running Tests

From the project root:

```bash
pytest
```

Install test dependencies first if needed:

```bash
pip install -r requirements.txt
```

### Integration tests

```bash
pytest -m integration
pytest -m "integration and not external"
pytest --cov=core_engine --cov-report=term-missing
```

### Opt-in / opt-out

Contacts without opt events are treated as opted-in by default. Use `record_opt_out()` / `record_opt_in()` in `core_engine.services.consent_service` to track consent. Blacklisted contacts never receive messages. The queue manager blocks enqueue when consent is missing.

### Roles and audit

JWT login via `POST /auth/token` with users:

| Username | Password | Role |
|----------|----------|------|
| admin | admin123 | admin |
| operator | operator123 | operator |
| viewer | viewer123 | viewer |

Sensitive operations require the matching role (`core_engine.services.rbac.requires_role`). Audit events are stored in `audit_logs` via `record_audit()` and can be listed at `GET /audit/logs` (admin only).

## Monitoring & Metrics

The API exposes Prometheus metrics at `GET /metrics` (no authentication required). Docker maps the API to port **8001**, so locally:

```text
http://localhost:8001/metrics
```

### KPI metrics (message pipeline)

| Metric | Type | Description |
|--------|------|-------------|
| `messages_queued_total` | Counter | Messages enqueued (labels: `platform`, `account_id`) |
| `messages_sent_success_total` | Counter | Successful sends (normal mode only) |
| `messages_sent_failed_total` | Counter | Failed sends with `reason` label |
| `rate_limit_hits_total` | Counter | Rate-limit events |
| `message_processing_seconds` | Histogram | End-to-end dispatch processing time |

Dry-run mode does not increment queue or send counters. Shadow mode records enqueue and processing time but not successful sends.

### Prometheus scraping

Prometheus in `docker-compose.yml` scrapes `core_api:8000/metrics` every 15s (see `monitoring/prometheus/prometheus.yml`). Grafana dashboards are provisioned from `monitoring/grafana/`.

Key operational KPIs to watch:

- **Queue pressure** — `mmp_queue_pending` and `messages_queued_total`
- **Delivery health** — `messages_sent_success_total` vs `messages_sent_failed_total`
- **Rate limiting** — `rate_limit_hits_total`
- **Latency** — `message_processing_seconds` histogram

See `monitoring/README.md` for Grafana URLs and alert rules.

### Load and chaos tests

Routine `pytest` excludes long-running stress and failure-injection suites (`pytest.ini` uses `-m "not stress and not chaos"`).

```bash
# Default unit + integration suite
pytest

# Load tests (tune volume/time via env)
STRESS_MESSAGE_COUNT=1000 STRESS_MAX_SECONDS=10 pytest -m stress

# Failure-injection tests
pytest -m chaos

# Everything
pytest -m ""
```

| Variable | Default | Purpose |
|----------|---------|---------|
| `STRESS_MESSAGE_COUNT` | 1000 | Messages in full pipeline load test |
| `STRESS_MAX_SECONDS` | 10 | Max wall time for load test |
| `STRESS_ENQUEUE_ONLY_COUNT` | 500 | Messages in enqueue-only stress test |
