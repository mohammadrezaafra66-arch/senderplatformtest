"""Rubika Phase 4 — canonical failure taxonomy / classifier.

Classifies connector and preflight outcomes into application-observed
categories. These are NOT official Rubika platform reputation signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RubikaFailureCategory(str, Enum):
    AUTH = "auth"
    SESSION = "session"
    RATE_LIMIT = "rate_limit"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    RECIPIENT = "recipient"
    CONFIG = "config"
    DEPENDENCY_REDIS = "dependency_redis"
    DEPENDENCY_DB = "dependency_db"
    POLICY = "policy"
    UNKNOWN = "unknown"


class RubikaFailureSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class RubikaFailureEvent:
    account_id: int | None
    campaign_id: int | str | None
    message_id: int | str | None
    mode: str | None
    category: RubikaFailureCategory
    code: str
    severity: RubikaFailureSeverity
    retryable: bool
    occurred_at: str
    source: str
    details: dict[str, Any] = field(default_factory=dict)


# Map error_code / preflight code → (category, severity, default_retryable override optional)
_CODE_MAP: dict[str, tuple[RubikaFailureCategory, RubikaFailureSeverity]] = {
    # Auth / session
    "rubika_unauthorized": (RubikaFailureCategory.AUTH, RubikaFailureSeverity.CRITICAL),
    "rubika_user_session_invalid": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "rubika_user_session_missing": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "rubika_session_missing": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "rubika_session_invalid": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "rubika_session_decrypt_failed": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "SESSION_MISSING": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "SESSION_INVALID": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "SESSION_DECRYPT_FAILED": (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL),
    "ACCOUNT_REQUIRES_LOGIN": (RubikaFailureCategory.AUTH, RubikaFailureSeverity.CRITICAL),
    "ACCOUNT_BANNED": (RubikaFailureCategory.AUTH, RubikaFailureSeverity.CRITICAL),
    "rubika_account_banned": (RubikaFailureCategory.AUTH, RubikaFailureSeverity.CRITICAL),
    "rubika_account_requires_login": (RubikaFailureCategory.AUTH, RubikaFailureSeverity.CRITICAL),
    # Rate
    "rubika_rate_limited": (RubikaFailureCategory.RATE_LIMIT, RubikaFailureSeverity.WARNING),
    "rubika_user_rate_limited": (RubikaFailureCategory.RATE_LIMIT, RubikaFailureSeverity.WARNING),
    # Transport / timeout
    "rubika_timeout": (RubikaFailureCategory.TIMEOUT, RubikaFailureSeverity.WARNING),
    "rubika_transport_error": (RubikaFailureCategory.TRANSPORT, RubikaFailureSeverity.WARNING),
    "rubika_http_error": (RubikaFailureCategory.TRANSPORT, RubikaFailureSeverity.WARNING),
    "rubika_bad_response": (RubikaFailureCategory.TRANSPORT, RubikaFailureSeverity.WARNING),
    "rubika_api_error": (RubikaFailureCategory.TRANSPORT, RubikaFailureSeverity.WARNING),
    "rubika_user_api_error": (RubikaFailureCategory.TRANSPORT, RubikaFailureSeverity.WARNING),
    "rubika_user_unexpected_error": (RubikaFailureCategory.UNKNOWN, RubikaFailureSeverity.WARNING),
    # Recipient
    "rubika_user_contact_missing": (RubikaFailureCategory.RECIPIENT, RubikaFailureSeverity.INFO),
    "rubika_user_phone_not_resolved": (RubikaFailureCategory.RECIPIENT, RubikaFailureSeverity.INFO),
    "rubika_chat_id_required": (RubikaFailureCategory.RECIPIENT, RubikaFailureSeverity.INFO),
    # Config
    "rubika_config_invalid": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    "CONFIG_INVALID": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    "USER_ACCOUNT_DISABLED": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    "DELIVERY_MODE_DISABLED": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    "rubika_user_account_disabled": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    "rubika_delivery_mode_disabled": (RubikaFailureCategory.CONFIG, RubikaFailureSeverity.CRITICAL),
    # Dependency
    "REDIS_UNAVAILABLE": (RubikaFailureCategory.DEPENDENCY_REDIS, RubikaFailureSeverity.CRITICAL),
    "rubika_redis_unavailable": (RubikaFailureCategory.DEPENDENCY_REDIS, RubikaFailureSeverity.CRITICAL),
    # Policy (not account guilt)
    "COOLDOWN_ACTIVE": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "HOURLY_CAP_REACHED": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "DAILY_CAP_REACHED": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "MIN_INTERVAL_ACTIVE": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "OUTSIDE_SEND_WINDOW": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "ACCOUNT_THROTTLED": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.WARNING),
    "ACCOUNT_QUARANTINED": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.CRITICAL),
    "RUBIKA_CIRCUIT_OPEN": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.CRITICAL),
    "rubika_cooldown_active": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "rubika_hourly_cap_reached": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "rubika_daily_cap_reached": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "rubika_min_interval_active": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "rubika_outside_send_window": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.INFO),
    "rubika_account_throttled": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.WARNING),
    "rubika_account_quarantined": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.CRITICAL),
    "rubika_circuit_open": (RubikaFailureCategory.POLICY, RubikaFailureSeverity.CRITICAL),
}


_DEPENDENCY_CATEGORIES = frozenset({
    RubikaFailureCategory.DEPENDENCY_REDIS,
    RubikaFailureCategory.DEPENDENCY_DB,
    RubikaFailureCategory.POLICY,
    RubikaFailureCategory.CONFIG,
    RubikaFailureCategory.RECIPIENT,
})


def _now_iso(clock: datetime | None = None) -> str:
    now = clock or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).isoformat()


def classify_rubika_failure(
    code: str | None,
    *,
    retryable: bool = False,
    account_id: int | None = None,
    campaign_id: int | str | None = None,
    message_id: int | str | None = None,
    mode: str | None = None,
    source: str = "connector",
    details: dict[str, Any] | None = None,
    clock: datetime | None = None,
) -> RubikaFailureEvent:
    raw = (code or "UNKNOWN").strip()
    mapped = _CODE_MAP.get(raw) or _CODE_MAP.get(raw.upper()) or _CODE_MAP.get(raw.lower())
    if mapped is None:
        # Heuristic fallbacks
        low = raw.lower()
        if "redis" in low:
            mapped = (RubikaFailureCategory.DEPENDENCY_REDIS, RubikaFailureSeverity.CRITICAL)
        elif "timeout" in low:
            mapped = (RubikaFailureCategory.TIMEOUT, RubikaFailureSeverity.WARNING)
        elif "session" in low or "login" in low:
            mapped = (RubikaFailureCategory.SESSION, RubikaFailureSeverity.CRITICAL)
        elif "rate" in low:
            mapped = (RubikaFailureCategory.RATE_LIMIT, RubikaFailureSeverity.WARNING)
        else:
            mapped = (RubikaFailureCategory.UNKNOWN, RubikaFailureSeverity.WARNING)

    category, severity = mapped
    safe_details = {
        k: v
        for k, v in (details or {}).items()
        if k.lower() not in {"token", "session", "password", "auth", "private_key", "otp"}
    }
    return RubikaFailureEvent(
        account_id=account_id,
        campaign_id=campaign_id,
        message_id=message_id,
        mode=mode,
        category=category,
        code=raw,
        severity=severity,
        retryable=bool(retryable),
        occurred_at=_now_iso(clock),
        source=source,
        details=safe_details,
    )


def is_account_guilt_category(category: RubikaFailureCategory) -> bool:
    """True when failure may escalate account health (not dependency/policy)."""
    return category not in _DEPENDENCY_CATEGORIES


def is_session_auth_category(category: RubikaFailureCategory) -> bool:
    return category in (RubikaFailureCategory.AUTH, RubikaFailureCategory.SESSION)
