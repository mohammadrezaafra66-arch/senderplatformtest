"""Central Rubika delivery-mode contract (Phase 1).

Single source of truth for:
- allowed modes (bot_api / user_account)
- normalization / rejection of invalid values
- mode → SessionType mapping

Workers and core_engine must import from here — do not duplicate the mapping.
"""

from __future__ import annotations

from core_engine.models import SessionType

RUBIKA_DELIVERY_MODES = frozenset({"bot_api", "user_account"})
RUBIKA_MODE_BOT_API = "bot_api"
RUBIKA_MODE_USER_ACCOUNT = "user_account"


def normalize_rubika_delivery_mode(value: object | None) -> str:
    """Normalize and validate Rubika delivery mode. Rejects unknown values."""
    mode = str(value if value is not None else RUBIKA_MODE_BOT_API).strip().lower()
    if mode not in RUBIKA_DELIVERY_MODES:
        raise ValueError(
            "RUBIKA_DELIVERY_MODE must be 'bot_api' or 'user_account'."
        )
    return mode


def resolve_rubika_delivery_mode(
    *,
    rubika_delivery_mode: str | None = None,
) -> str:
    """Return the effective Rubika delivery mode (explicit override or settings)."""
    if rubika_delivery_mode is not None:
        return normalize_rubika_delivery_mode(rubika_delivery_mode)
    from core_engine.config import get_settings

    return normalize_rubika_delivery_mode(get_settings().RUBIKA_DELIVERY_MODE)


def is_rubika_user_account_enabled() -> bool:
    from core_engine.config import get_settings

    return bool(get_settings().RUBIKA_USER_ACCOUNT_ENABLED)


def rubika_required_session_type(delivery_mode: str) -> SessionType:
    """Central mode → session-type contract."""
    mode = normalize_rubika_delivery_mode(delivery_mode)
    if mode == RUBIKA_MODE_USER_ACCOUNT:
        return SessionType.RUBIKA_SESSION
    return SessionType.API_TOKEN


def assert_rubika_bot_api_registration_allowed() -> str:
    """Raise ValueError if bot-token registration is not allowed for current mode."""
    mode = resolve_rubika_delivery_mode()
    if mode != RUBIKA_MODE_BOT_API:
        raise ValueError(
            "Rubika bot API token registration requires RUBIKA_DELIVERY_MODE=bot_api. "
            f"Current mode is '{mode}'. Use the user-account OTP login flow instead."
        )
    return mode


def assert_rubika_user_login_allowed() -> str:
    """Raise ValueError if interactive user-account login is not allowed."""
    mode = resolve_rubika_delivery_mode()
    if mode != RUBIKA_MODE_USER_ACCOUNT:
        raise ValueError(
            "Rubika user-account login requires RUBIKA_DELIVERY_MODE=user_account. "
            f"Current mode is '{mode}'."
        )
    if not is_rubika_user_account_enabled():
        raise ValueError(
            "Rubika user-account login is disabled "
            "(RUBIKA_USER_ACCOUNT_ENABLED=false)."
        )
    return mode
