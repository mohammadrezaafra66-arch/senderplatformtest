"""Lightweight in-memory session for audit unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core_engine.models import AuditLog


@dataclass
class AuditFakeSession:
    logs: list[AuditLog] = field(default_factory=list)
    _next_id: int = 0

    def add(self, obj: Any) -> None:
        if isinstance(obj, AuditLog):
            self._next_id += 1
            obj.id = self._next_id
            if obj.timestamp is None:
                obj.timestamp = datetime.utcnow()
            self.logs.append(obj)

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        return None

    def query(self, model: Any) -> _AuditQuery:
        if model is not AuditLog:
            raise TypeError(f"Unsupported model: {model!r}")
        return _AuditQuery(self)


class _AuditQuery:
    def __init__(self, session: AuditFakeSession) -> None:
        self._session = session
        self._limit: int | None = None

    def order_by(self, *args: Any) -> _AuditQuery:
        return self

    def limit(self, value: int) -> _AuditQuery:
        self._limit = value
        return self

    def all(self) -> list[AuditLog]:
        rows = sorted(
            self._session.logs,
            key=lambda item: (item.timestamp, item.id or 0),
            reverse=True,
        )
        if self._limit is not None:
            return rows[: self._limit]
        return rows
