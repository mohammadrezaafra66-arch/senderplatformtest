"""Lightweight in-memory session for consent unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core_engine.models import AuditLog, Contact, OptEvent


@dataclass
class _ContactState:
    contact_id: int
    blacklisted: bool = False


class _ContactProxy:
    def __init__(self, state: _ContactState) -> None:
        self._state = state

    @property
    def id(self) -> int:
        return self._state.contact_id

    @property
    def blacklisted(self) -> bool:
        return self._state.blacklisted

    @blacklisted.setter
    def blacklisted(self, value: bool) -> None:
        self._state.blacklisted = value


@dataclass
class ConsentFakeSession:
  contacts: dict[int, _ContactState] = field(default_factory=dict)
  events: list[OptEvent] = field(default_factory=list)
  audit_logs: list[AuditLog] = field(default_factory=list)
  _event_id: int = 0
  _audit_id: int = 0

  def seed_contact(self, contact_id: int = 1, *, blacklisted: bool = False) -> None:
      self.contacts[contact_id] = _ContactState(contact_id=contact_id, blacklisted=blacklisted)

  def get(self, model: Any, contact_id: int) -> Any:
      if model is not Contact:
          return None
      state = self.contacts.get(contact_id)
      if state is None:
          return None
      return _ContactProxy(state)

  def query(self, model: Any) -> _OptEventQuery | _AuditLogQuery:
      if model is OptEvent:
          return _OptEventQuery(self)
      if model is AuditLog:
          return _AuditLogQuery(self)
      raise TypeError(f"Unsupported model for fake query: {model!r}")

  def add(self, obj: Any) -> None:
      if isinstance(obj, OptEvent):
          self._event_id += 1
          obj.id = self._event_id
          if obj.timestamp is None:
              obj.timestamp = datetime.utcnow()
          self.events.append(obj)
      elif isinstance(obj, AuditLog):
          self._audit_id += 1
          obj.id = self._audit_id
          if obj.timestamp is None:
              obj.timestamp = datetime.utcnow()
          self.audit_logs.append(obj)

  def flush(self) -> None:
      return None

  def commit(self) -> None:
      return None


class _OptEventQuery:
    def __init__(self, session: ConsentFakeSession) -> None:
        self._session = session
        self._contact_id: int | None = None

    def filter(self, *args: Any, **kwargs: Any) -> _OptEventQuery:
        for arg in args:
            left = getattr(arg, "left", None)
            right = getattr(arg, "right", None)
            if left is not None and getattr(left, "key", None) == "contact_id":
                self._contact_id = int(right.value if hasattr(right, "value") else right)
        return self

    def order_by(self, *args: Any) -> _OptEventQuery:
        return self

    def all(self) -> list[OptEvent]:
        events = [
            event
            for event in self._session.events
            if self._contact_id is None or event.contact_id == self._contact_id
        ]
        return sorted(events, key=lambda item: (item.timestamp, item.id or 0), reverse=True)


class _AuditLogQuery:
    def __init__(self, session: ConsentFakeSession) -> None:
        self._session = session
        self._limit: int | None = None

    def order_by(self, *args: Any) -> _AuditLogQuery:
        return self

    def limit(self, value: int) -> _AuditLogQuery:
        self._limit = value
        return self

    def all(self) -> list[AuditLog]:
        rows = sorted(
            self._session.audit_logs,
            key=lambda item: (item.timestamp, item.id or 0),
            reverse=True,
        )
        if self._limit is not None:
            return rows[: self._limit]
        return rows
