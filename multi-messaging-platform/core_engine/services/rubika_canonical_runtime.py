"""L7 — Controlled Rubika canonical session runtime (off / shadow / enforce).

Legacy max(id) remains authoritative unless mode=enforce AND account is allowlisted.
Shadow never mutates DB/Redis and never changes the returned session.
Enforce fails closed for allowlisted accounts (no silent legacy fallback).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from core_engine.models import ChannelSession, SessionType
from core_engine.services.rubika_canonical_session import (
    CanonicalSessionError,
    load_canonical_rubika_session,
)
from core_engine.services.session_storage import load_channel_session_plaintext

logger = logging.getLogger("core_engine.services.rubika_canonical_runtime")

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
VALID_MODES = frozenset({MODE_OFF, MODE_SHADOW, MODE_ENFORCE})

SHADOW_MATCH = "SHADOW_MATCH"
SHADOW_NO_ACTIVE = "SHADOW_NO_ACTIVE"
SHADOW_DIFFERENT_SESSION = "SHADOW_DIFFERENT_SESSION"
SHADOW_CANONICAL_ERROR = "SHADOW_CANONICAL_ERROR"

MALFORMED_ALLOWLIST = "MALFORMED_ALLOWLIST"
NO_OTP_OR_SEND_FROM_MODE = True  # documentation / test sentinel


@dataclass(frozen=True)
class AllowlistParseResult:
    ok: bool
    account_ids: frozenset[int]
    error: str | None = None
    raw: str = ""


@dataclass(frozen=True)
class ShadowComparison:
    account_id: int
    legacy_selected_session_id: int | None
    canonical_selected_session_id: int | None
    canonical_error: str | None
    match: bool
    metric: str

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "legacy_selected_session_id": self.legacy_selected_session_id,
            "canonical_selected_session_id": self.canonical_selected_session_id,
            "canonical_error": self.canonical_error,
            "match": self.match,
            "metric": self.metric,
        }


@dataclass
class ShadowMetrics:
    """In-process counters for tests / observability (never secrets)."""

    counts: dict[str, int] = field(
        default_factory=lambda: {
            SHADOW_MATCH: 0,
            SHADOW_NO_ACTIVE: 0,
            SHADOW_DIFFERENT_SESSION: 0,
            SHADOW_CANONICAL_ERROR: 0,
        }
    )
    last: ShadowComparison | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, comparison: ShadowComparison) -> None:
        with self._lock:
            self.counts[comparison.metric] = int(self.counts.get(comparison.metric, 0)) + 1
            self.last = comparison

    def reset(self) -> None:
        with self._lock:
            for key in list(self.counts):
                self.counts[key] = 0
            self.last = None


_SHADOW_METRICS = ShadowMetrics()
_SHADOW_HOOKS: list[Callable[[ShadowComparison], None]] = []


def get_shadow_metrics() -> ShadowMetrics:
    return _SHADOW_METRICS


def register_shadow_hook(hook: Callable[[ShadowComparison], None]) -> None:
    _SHADOW_HOOKS.append(hook)


def clear_shadow_hooks() -> None:
    _SHADOW_HOOKS.clear()


def parse_canonical_account_allowlist(raw: str | None) -> AllowlistParseResult:
    text = "" if raw is None else str(raw).strip()
    if not text:
        return AllowlistParseResult(ok=True, account_ids=frozenset(), raw=text)
    ids: set[int] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if not token.isdigit() and not (token.startswith("-") and token[1:].isdigit()):
            return AllowlistParseResult(
                ok=False,
                account_ids=frozenset(),
                error=f"{MALFORMED_ALLOWLIST}:{token[:32]}",
                raw=text,
            )
        value = int(token)
        if value <= 0:
            return AllowlistParseResult(
                ok=False,
                account_ids=frozenset(),
                error=f"{MALFORMED_ALLOWLIST}:non_positive",
                raw=text,
            )
        ids.add(value)
    return AllowlistParseResult(ok=True, account_ids=frozenset(ids), raw=text)


def resolve_canonical_session_mode(raw: str | None) -> str:
    mode = str(raw or MODE_OFF).strip().lower() or MODE_OFF
    if mode not in VALID_MODES:
        # Fail safe: unknown mode → off (legacy).
        logger.error("event=canonical_mode_invalid mode=%s fallback=off", mode[:32])
        return MODE_OFF
    return mode


def _settings_mode_and_allowlist() -> tuple[str, AllowlistParseResult]:
    from core_engine.config import get_settings

    settings = get_settings()
    mode = resolve_canonical_session_mode(
        getattr(settings, "RUBIKA_CANONICAL_SESSION_MODE", MODE_OFF)
    )
    allowlist = parse_canonical_account_allowlist(
        getattr(settings, "RUBIKA_CANONICAL_SESSION_ACCOUNT_IDS", "")
    )
    if not allowlist.ok:
        logger.error(
            "event=canonical_allowlist_malformed error=%s mode=%s "
            "action=treat_allowlist_empty_nobody_enforced",
            allowlist.error,
            mode,
        )
    return mode, allowlist


def account_in_canonical_cohort(account_id: int, db: Session | None = None) -> bool:
    mode, allowlist = _settings_mode_and_allowlist()
    if mode == MODE_OFF:
        return False
    from core_engine.config import get_settings
    from core_engine.services.rubika_l17_automation import (
        ENFORCE_SCOPE_CANONICAL_ACTIVE,
        account_should_auto_enforce,
        normalize_enforce_scope,
    )

    scope = normalize_enforce_scope(
        getattr(get_settings(), "RUBIKA_CANONICAL_SESSION_SCOPE", None)
    )
    if scope == ENFORCE_SCOPE_CANONICAL_ACTIVE:
        if db is None:
            return False
        return account_should_auto_enforce(db, int(account_id))
    if not allowlist.ok:
        return False
    return int(account_id) in allowlist.account_ids


def enforce_applies_to_account(account_id: int, db: Session | None = None) -> bool:
    mode, allowlist = _settings_mode_and_allowlist()
    if mode != MODE_ENFORCE:
        return False
    from core_engine.config import get_settings
    from core_engine.services.rubika_l17_automation import (
        ENFORCE_SCOPE_CANONICAL_ACTIVE,
        account_should_auto_enforce,
        normalize_enforce_scope,
    )

    scope = normalize_enforce_scope(
        getattr(get_settings(), "RUBIKA_CANONICAL_SESSION_SCOPE", None)
    )
    if scope == ENFORCE_SCOPE_CANONICAL_ACTIVE:
        if db is None:
            return False
        return account_should_auto_enforce(db, int(account_id))
    if not allowlist.ok or not allowlist.account_ids:
        return False
    return int(account_id) in allowlist.account_ids


def shadow_applies_to_account(account_id: int, db: Session | None = None) -> bool:
    mode, allowlist = _settings_mode_and_allowlist()
    if mode != MODE_SHADOW:
        return False
    from core_engine.config import get_settings
    from core_engine.services.rubika_l17_automation import (
        ENFORCE_SCOPE_CANONICAL_ACTIVE,
        account_should_auto_enforce,
        normalize_enforce_scope,
    )

    scope = normalize_enforce_scope(
        getattr(get_settings(), "RUBIKA_CANONICAL_SESSION_SCOPE", None)
    )
    if scope == ENFORCE_SCOPE_CANONICAL_ACTIVE:
        if db is None:
            return False
        return account_should_auto_enforce(db, int(account_id))
    if not allowlist.ok:
        return False
    # Empty allowlist in shadow = observe nobody (safe default).
    if not allowlist.account_ids:
        return False
    return int(account_id) in allowlist.account_ids


def select_legacy_rubika_session_row(
    db: Session, account_id: int
) -> ChannelSession | None:
    """Legacy max(id) selector — read-only."""
    return (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == int(account_id),
            ChannelSession.session_type == SessionType.RUBIKA_SESSION,
        )
        .order_by(ChannelSession.id.desc())
        .first()
    )


def compare_legacy_vs_canonical(
    db: Session,
    account_id: int,
    *,
    legacy_row: ChannelSession | None,
) -> ShadowComparison:
    legacy_id = int(legacy_row.id) if legacy_row is not None else None
    canonical_id: int | None = None
    canonical_error: str | None = None
    try:
        loaded = load_canonical_rubika_session(
            db,
            int(account_id),
            require_identity_binding=False,
            return_plaintext=False,
        )
        canonical_id = int(loaded.session_id)
    except CanonicalSessionError as exc:
        canonical_error = str(exc.code)
    except Exception as exc:  # noqa: BLE001
        canonical_error = type(exc).__name__

    if canonical_error == "NO_ACTIVE_SESSION":
        metric = SHADOW_NO_ACTIVE
        match = False
    elif canonical_error:
        metric = SHADOW_CANONICAL_ERROR
        match = False
    elif legacy_id is not None and canonical_id == legacy_id:
        metric = SHADOW_MATCH
        match = True
    else:
        metric = SHADOW_DIFFERENT_SESSION
        match = False

    return ShadowComparison(
        account_id=int(account_id),
        legacy_selected_session_id=legacy_id,
        canonical_selected_session_id=canonical_id,
        canonical_error=canonical_error,
        match=match,
        metric=metric,
    )


def _emit_shadow(comparison: ShadowComparison) -> None:
    _SHADOW_METRICS.record(comparison)
    logger.info(
        "event=rubika_canonical_shadow account_id=%s legacy_session_id=%s "
        "canonical_session_id=%s canonical_error=%s match=%s metric=%s",
        comparison.account_id,
        comparison.legacy_selected_session_id,
        comparison.canonical_selected_session_id,
        comparison.canonical_error,
        comparison.match,
        comparison.metric,
    )
    for hook in list(_SHADOW_HOOKS):
        try:
            hook(comparison)
        except Exception:  # noqa: BLE001
            logger.exception("event=rubika_canonical_shadow_hook_failed")


@dataclass(frozen=True)
class RuntimeSessionSelection:
    account_id: int
    session_id: int
    plaintext: bytes
    source: str  # legacy | canonical_enforce
    mode: str


def load_rubika_runtime_session(
    db: Session,
    account_id: int,
    *,
    decrypt: Callable[[ChannelSession], bytes] | None = None,
) -> RuntimeSessionSelection:
    """Resolve Rubika session plaintext for runtime dispatch.

    - off: legacy max(id)
    - shadow: legacy authoritative + read-only canonical compare for cohort
    - enforce: canonical only for allowlisted accounts (fail closed);
      non-allowlisted keep legacy
    """
    decrypt_fn = decrypt or load_channel_session_plaintext
    mode, allowlist = _settings_mode_and_allowlist()
    aid = int(account_id)

    # Enforce path — fail closed, never fall back to max(id).
    if enforce_applies_to_account(aid, db):
        try:
            loaded = load_canonical_rubika_session(
                db,
                aid,
                require_identity_binding=True,
                return_plaintext=True,
            )
        except CanonicalSessionError:
            raise
        if loaded.plaintext is None:
            raise CanonicalSessionError("SESSION_DECRYPT_FAILED", "empty plaintext")
        return RuntimeSessionSelection(
            account_id=aid,
            session_id=int(loaded.session_id),
            plaintext=loaded.plaintext,
            source="canonical_enforce",
            mode=mode,
        )

    # Legacy path (also used for shadow authoritative result).
    legacy = select_legacy_rubika_session_row(db, aid)
    if legacy is None or not legacy.ciphertext:
        raise CanonicalSessionError(
            "NO_LEGACY_SESSION",
            f"No encrypted legacy session for account {aid}",
        )

    if shadow_applies_to_account(aid, db):
        # Read-only compare — must not change selection or mutate state.
        comparison = compare_legacy_vs_canonical(db, aid, legacy_row=legacy)
        _emit_shadow(comparison)

    plaintext = decrypt_fn(legacy)
    return RuntimeSessionSelection(
        account_id=aid,
        session_id=int(legacy.id),
        plaintext=plaintext,
        source="legacy",
        mode=mode,
    )
