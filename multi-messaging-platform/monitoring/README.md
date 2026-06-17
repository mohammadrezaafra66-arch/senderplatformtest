# Multi Messaging Platform — Monitoring

## URLs

| Service | URL |
|---------|-----|
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3002 |
| API metrics | http://localhost:8001/metrics |

Grafana runs on host port **3002** (not 3001) because port 3001 was already in use by another local service (`afrakala-local-studio`).

## Grafana login

- **Username:** `admin`
- **Password:** `admin`

Datasource **Prometheus** (`http://prometheus:9090`) and dashboard **Multi Messaging Platform Dashboard** are auto-provisioned on startup.

## Important metrics

| Metric | Description |
|--------|-------------|
| `mmp_http_requests_total` | HTTP request counter (`method`, `path`, `status_code`) |
| `mmp_http_request_duration_seconds` | Request latency histogram |
| `mmp_campaigns_total` | Total campaigns |
| `mmp_messages_sent` / `mmp_messages_failed` | Message attempt stats |
| `mmp_queue_pending` | Redis queue depth (`queue_name` label) |
| `mmp_redis_available` | `1` = Redis up, `0` = down |
| `mmp_db_available` | `1` = DB up, `0` = down |
| `mmp_kill_switch_enabled` | `1` = kill switch on, `0` = off |
| `mmp_dashboard_ws_connections_active` | Active dashboard WebSocket clients |

## Alert rules

Defined in `monitoring/prometheus/alert_rules.yml`:

| Alert | Condition | Duration | Severity |
|-------|-----------|----------|----------|
| **RedisDown** | `mmp_redis_available == 0` | 30s | critical |
| **DatabaseDown** | `mmp_db_available == 0` | 30s | critical |
| **KillSwitchEnabled** | `mmp_kill_switch_enabled == 1` | 1m | warning |
| **QueueBacklogHigh** | `mmp_queue_pending > 100` | 2m | warning |
| **ApiErrorsHigh** | `sum(rate(mmp_http_requests_total{status_code=~"5.."}[1m])) > 0` | 1m | critical |

No WebSocket alert is defined: `mmp_dashboard_ws_connections_active` is tracked for visibility, but no safe high-water threshold was specified for production alerting.

## Useful Prometheus API checks

```powershell
# Loaded rules
Invoke-RestMethod -Uri "http://localhost:9090/api/v1/rules" | ConvertTo-Json -Depth 20

# Active alerts
Invoke-RestMethod -Uri "http://localhost:9090/api/v1/alerts" | ConvertTo-Json -Depth 20

# Scrape targets
Invoke-RestMethod -Uri "http://localhost:9090/api/v1/targets" | ConvertTo-Json -Depth 10
```

In Prometheus UI: **Alerts** and **Status → Rules**.

## Kill Switch alert test

1. Enable kill switch:

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/controls/kill-switch" -Method POST `
  -ContentType "application/json" `
  -Body '{"enabled":true}' | ConvertTo-Json -Depth 10
```

2. Wait **60–90 seconds** (rule `for: 1m` plus scrape/eval delay).

3. Check alerts:

```powershell
Invoke-RestMethod -Uri "http://localhost:9090/api/v1/alerts" | ConvertTo-Json -Depth 20
```

Expect **KillSwitchEnabled** in `pending` or `firing` state.

4. Disable kill switch after the test:

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/controls/kill-switch" -Method POST `
  -ContentType "application/json" `
  -Body '{"enabled":false}' | ConvertTo-Json -Depth 10
```

## Restart monitoring stack

```powershell
docker compose up -d --build
```

After changing `prometheus.yml` or `alert_rules.yml`, restart Prometheus:

```powershell
docker compose restart prometheus
```
