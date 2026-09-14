"""Rubika Account↔GUID identity binding helpers (L1/L2).

Default: GUID must not silently change once bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from core_engine.models import Account, RubikaIdentityStatus

IDENTITY_OK = "IDENTITY_OK"
IDENTITY_BOUND = "IDENTITY_BOUND"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
IDENTITY_GUID_MISSING = "IDENTITY_GUID_MISSING"
IDENTITY_ACCOUNT_MISSING = "IDENTITY_ACCOUNT_MISSING"


@dataclass(frozen=True)
class IdentityBindResult:
    ok: bool
    code: str
    bound_guid: str | None = None
    newly_bound: bool = False


def bind_or_verify_rubika_identity(
    account: Account | None,
    guid: str | None,
    *,
    allow_bind_if_unbound: bool = True,
    clock: datetime | None = None,
) -> IdentityBindResult:
    """Bind GUID on first proven identity, or verify match thereafter.

    Does NOT overwrite an existing different GUID.
    Caller must only invoke after authenticated identity is proven.
    """
    if account is None:
        return IdentityBindResult(ok=False, code=IDENTITY_ACCOUNT_MISSING)

    cleaned = str(guid or "").strip()
    if not cleaned:
        return IdentityBindResult(ok=False, code=IDENTITY_GUID_MISSING)

    existing = str(account.rubika_guid or "").strip() or None
    status = account.rubika_identity_status

    if existing is None or status in {None, RubikaIdentityStatus.UNBOUND}:
        if not allow_bind_if_unbound:
            return IdentityBindResult(ok=False, code=IDENTITY_GUID_MISSING)
        account.rubika_guid = cleaned
        account.rubika_identity_status = RubikaIdentityStatus.VERIFIED
        account.rubika_identity_verified_at = clock or datetime.now(timezone.utc)
        return IdentityBindResult(
            ok=True,
            code=IDENTITY_BOUND,
            bound_guid=cleaned,
            newly_bound=True,
        )

    if existing == cleaned:
        if status != RubikaIdentityStatus.VERIFIED and status != RubikaIdentityStatus.OPERATOR_OVERRIDE:
            account.rubika_identity_status = RubikaIdentityStatus.VERIFIED
            if account.rubika_identity_verified_at is None:
                account.rubika_identity_verified_at = clock or datetime.now(timezone.utc)
        return IdentityBindResult(ok=True, code=IDENTITY_OK, bound_guid=existing)

    # Mismatch — lock, do not overwrite.
    account.rubika_identity_status = RubikaIdentityStatus.MISMATCH_LOCKED
    return IdentityBindResult(
        ok=False,
        code=IDENTITY_MISMATCH,
        bound_guid=existing,
        newly_bound=False,
    )
