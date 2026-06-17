"""Role-based access control helpers."""

from __future__ import annotations

from core_engine.api.auth import require_roles
from core_engine.models import RoleType


def requires_role(*allowed_roles: RoleType):
    """FastAPI dependency restricting access to the given roles."""
    allowed = tuple(role.value for role in allowed_roles)
    return require_roles(*allowed)
