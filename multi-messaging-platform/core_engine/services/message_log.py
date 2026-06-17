"""In-memory message dispatch log for dry-run and shadow mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core_engine.models import SendStatus


@dataclass
class MessageLog:
    contact_id: int
    campaign_id: int
    platform: str
    chat_identifier: str
    original_chat_identifier: str
    message_text: str
    status: str
    media_url: str | None = None
    attempt_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "campaign_id": self.campaign_id,
            "platform": self.platform,
            "chat_identifier": self.chat_identifier,
            "original_chat_identifier": self.original_chat_identifier,
            "message_text": self.message_text,
            "status": self.status,
            "media_url": self.media_url,
            "attempt_count": self.attempt_count,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


_message_logs: list[MessageLog] = []


def record_message_log(
    *,
    contact_id: int,
    campaign_id: int,
    platform: str,
    chat_identifier: str,
    original_chat_identifier: str,
    message_text: str,
    status: SendStatus | str,
    media_url: str | None = None,
    attempt_count: int = 0,
    metadata: dict[str, Any] | None = None,
) -> MessageLog:
    status_value = status.value if isinstance(status, SendStatus) else str(status)
    entry = MessageLog(
        contact_id=contact_id,
        campaign_id=campaign_id,
        platform=platform,
        chat_identifier=chat_identifier,
        original_chat_identifier=original_chat_identifier,
        message_text=message_text,
        status=status_value,
        media_url=media_url,
        attempt_count=attempt_count,
        metadata=metadata or {},
    )
    _message_logs.append(entry)
    return entry


def get_message_logs() -> list[MessageLog]:
    return list(_message_logs)


def clear_message_logs() -> None:
    _message_logs.clear()
