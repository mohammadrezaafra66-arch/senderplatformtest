"""Guarded MANUAL_LIVE_TEST helpers. Never imported by CI pytest collection."""

from __future__ import annotations

from core_engine.services.pilot_profiles import require_manual_live_confirmation


def refuse_unless_confirmed() -> None:
    require_manual_live_confirmation()
