"""Phase 7 runtime profiles and live-pilot confirmation guards.

Profiles are documentation + validation of intended safety meaning.
They do not send messages. Live confirmation is opt-in and fail-closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

LOCAL_TEST = "LOCAL_TEST"
PILOT_SHADOW = "PILOT_SHADOW"
PILOT_LIVE = "PILOT_LIVE"

# Canonical transport kill switch (worker router).
KILL_SWITCH = "REAL_MESSAGE_SENDING_ENABLED"

MANUAL_LIVE_ENV = "MANUAL_LIVE_TEST"
PILOT_CONFIRM_ENV = "PILOT_CONFIRM"
PILOT_CONFIRM_VALUE = "SEND"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE


@dataclass(frozen=True)
class PilotProfileSpec:
    name: str
    real_queue_push: bool
    real_message_sending: bool
    channel_connectors: bool
    dry_run: bool
    shadow_mode: bool
    ops_live_send_api: bool
    meaning: str


PROFILES: dict[str, PilotProfileSpec] = {
    LOCAL_TEST: PilotProfileSpec(
        name=LOCAL_TEST,
        real_queue_push=False,
        real_message_sending=False,
        channel_connectors=False,
        dry_run=False,
        shadow_mode=False,
        ops_live_send_api=False,
        meaning="Automated tests / local pytest. No real queue push, no transport.",
    ),
    PILOT_SHADOW: PilotProfileSpec(
        name=PILOT_SHADOW,
        real_queue_push=True,
        real_message_sending=False,
        channel_connectors=False,
        dry_run=False,
        shadow_mode=False,
        ops_live_send_api=False,
        meaning=(
            "Real DB/Redis, prepare/freeze/preflight/capacity, optional queue push. "
            "REAL_MESSAGE_SENDING_ENABLED remains false so Rubika transport is impossible."
        ),
    ),
    PILOT_LIVE: PilotProfileSpec(
        name=PILOT_LIVE,
        real_queue_push=True,
        real_message_sending=True,
        channel_connectors=True,
        dry_run=False,
        shadow_mode=False,
        ops_live_send_api=False,
        meaning=(
            "Narrowest owner-approved real transport. Kill switch ON. "
            "Do not enable until OWNER GATE. OPS live API stays off unless separately approved."
        ),
    ),
}


def transport_kill_switch_engaged(settings: Any) -> bool:
    """True when the canonical kill switch blocks live transport."""
    return not _as_bool(getattr(settings, KILL_SWITCH, False))


def classify_profile(settings: Any, *, queue_push: bool | None = None) -> str:
    sending = _as_bool(getattr(settings, "REAL_MESSAGE_SENDING_ENABLED", False))
    connectors = _as_bool(getattr(settings, "CHANNEL_CONNECTORS_ENABLED", False))
    dry = _as_bool(getattr(settings, "DRY_RUN", False))
    shadow = _as_bool(getattr(settings, "SHADOW_MODE", False))
    push = _as_bool(queue_push, False) if queue_push is not None else _as_bool(
        getattr(settings, "REAL_QUEUE_PUSH_ENABLED", False)
    )
    if sending and connectors and not dry and not shadow and push:
        return PILOT_LIVE
    if not sending:
        if push:
            return PILOT_SHADOW
        return LOCAL_TEST
    return LOCAL_TEST


def require_manual_live_confirmation() -> None:
    """Refuse live external calls unless both confirmation flags are set."""
    if os.environ.get(MANUAL_LIVE_ENV) != "1":
        raise RuntimeError(
            "Refusing live call: set MANUAL_LIVE_TEST=1 (never used by CI)."
        )
    if os.environ.get(PILOT_CONFIRM_ENV) != PILOT_CONFIRM_VALUE:
        raise RuntimeError(
            "Refusing live call: set PILOT_CONFIRM=SEND after owner approval."
        )
