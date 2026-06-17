"""Prometheus metrics — تعریف، refresh و export."""

from __future__ import annotations

import logging

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import text

import core_engine.monitoring.metrics  # noqa: F401 — register KPI counters/histograms

logger = logging.getLogger(__name__)

HTTP_REQUESTS_TOTAL = Counter(
    "mmp_http_requests_total",
    "Total HTTP requests handled by the API",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "mmp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

CAMPAIGNS_TOTAL = Gauge("mmp_campaigns_total", "Total campaigns in database")
CAMPAIGNS_RUNNING = Gauge("mmp_campaigns_running", "Campaigns with running status")
CAMPAIGNS_PAUSED = Gauge("mmp_campaigns_paused", "Campaigns with paused status")
MESSAGES_TOTAL = Gauge("mmp_messages_total", "Total messages in database")
MESSAGES_SENT = Gauge("mmp_messages_sent", "Successful message attempts")
MESSAGES_FAILED = Gauge("mmp_messages_failed", "Failed message attempts")
ACCOUNTS_TOTAL = Gauge("mmp_accounts_total", "Total accounts in database")
ACCOUNTS_ACTIVE = Gauge("mmp_accounts_active", "Active accounts")
ACCOUNTS_BANNED = Gauge("mmp_accounts_banned", "Banned accounts")

QUEUE_PENDING = Gauge(
    "mmp_queue_pending",
    "Pending items in Redis queue",
    ["queue_name"],
)

REDIS_AVAILABLE = Gauge("mmp_redis_available", "Redis availability (1=up, 0=down)")
DB_AVAILABLE = Gauge("mmp_db_available", "Database availability (1=up, 0=down)")

KILL_SWITCH_ENABLED = Gauge(
    "mmp_kill_switch_enabled",
    "Kill switch state (1=enabled, 0=disabled)",
)

WS_CONNECTIONS_ACTIVE = Gauge(
    "mmp_dashboard_ws_connections_active",
    "Active dashboard WebSocket connections",
)

WS_MESSAGES_SENT_TOTAL = Counter(
    "mmp_dashboard_ws_messages_sent_total",
    "Total dashboard WebSocket snapshot messages sent",
)


def record_http_request(
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    status_label = str(status_code)
    HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status_code=status_label).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration_seconds)


def ws_connection_opened() -> None:
    WS_CONNECTIONS_ACTIVE.inc()


def ws_connection_closed() -> None:
    WS_CONNECTIONS_ACTIVE.dec()


def ws_message_sent() -> None:
    WS_MESSAGES_SENT_TOTAL.inc()


def _set_dashboard_gauges(summary: dict[str, int]) -> None:
    CAMPAIGNS_TOTAL.set(summary.get("campaigns_total", 0))
    CAMPAIGNS_RUNNING.set(summary.get("campaigns_running", 0))
    CAMPAIGNS_PAUSED.set(summary.get("campaigns_paused", 0))
    MESSAGES_TOTAL.set(summary.get("messages_total", 0))
    MESSAGES_SENT.set(summary.get("messages_sent", 0))
    MESSAGES_FAILED.set(summary.get("messages_failed", 0))
    ACCOUNTS_TOTAL.set(summary.get("accounts_total", 0))
    ACCOUNTS_ACTIVE.set(summary.get("accounts_active", 0))
    ACCOUNTS_BANNED.set(summary.get("accounts_banned", 0))


def _zero_dashboard_gauges() -> None:
    _set_dashboard_gauges(
        {
            "campaigns_total": 0,
            "campaigns_running": 0,
            "campaigns_paused": 0,
            "messages_total": 0,
            "messages_sent": 0,
            "messages_failed": 0,
            "accounts_total": 0,
            "accounts_active": 0,
            "accounts_banned": 0,
        }
    )


def _check_db_available() -> bool:
    from core_engine.database import SessionLocal

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("metrics: database unavailable: %s", exc)
        return False
    finally:
        db.close()


async def refresh_dynamic_metrics() -> None:
    """Refresh gauges from DB, Redis and control services before /metrics scrape."""
    from core_engine.database import SessionLocal
    from core_engine.services.control_service import get_kill_switch_enabled_for_snapshot
    from core_engine.services.dashboard_service import get_dashboard_summary
    from core_engine.services.redis_client import DASHBOARD_QUEUE_NAMES, get_dashboard_queue_pending

    db_ok = _check_db_available()
    DB_AVAILABLE.set(1 if db_ok else 0)

    if db_ok:
        db = SessionLocal()
        try:
            summary = get_dashboard_summary(db)
            _set_dashboard_gauges(summary)
        except Exception as exc:
            logger.warning("metrics: failed to load dashboard summary: %s", exc)
            DB_AVAILABLE.set(0)
            _zero_dashboard_gauges()
        finally:
            db.close()
    else:
        _zero_dashboard_gauges()

    try:
        queues, redis_connected = await get_dashboard_queue_pending()
        REDIS_AVAILABLE.set(1 if redis_connected else 0)
        queue_by_name = {str(item["name"]): int(item["pending"]) for item in queues}
        for name in DASHBOARD_QUEUE_NAMES:
            QUEUE_PENDING.labels(queue_name=name).set(queue_by_name.get(name, 0))
    except Exception as exc:
        logger.warning("metrics: failed to load queue metrics: %s", exc)
        REDIS_AVAILABLE.set(0)
        for name in DASHBOARD_QUEUE_NAMES:
            QUEUE_PENDING.labels(queue_name=name).set(0)

    try:
        if await _redis_ping_for_kill_switch():
            enabled = await get_kill_switch_enabled_for_snapshot()
            KILL_SWITCH_ENABLED.set(1 if enabled else 0)
        else:
            REDIS_AVAILABLE.set(0)
            KILL_SWITCH_ENABLED.set(0)
    except Exception as exc:
        logger.warning("metrics: failed to load kill switch metric: %s", exc)
        KILL_SWITCH_ENABLED.set(0)


async def _redis_ping_for_kill_switch() -> bool:
    from core_engine.services.redis_client import ping_redis

    return await ping_redis()


def get_metrics_output() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
