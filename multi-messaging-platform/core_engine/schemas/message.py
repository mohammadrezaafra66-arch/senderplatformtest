"""Contract schemas for channel worker message payloads and results."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

PlatformType = Literal["whatsapp", "telegram", "rubika", "bale"]
MessageStatus = Literal["success", "failed"]


class MessagePayload(BaseModel):
    contact_id: int
    campaign_id: int
    platform: PlatformType
    chat_identifier: str
    message_text: str
    media_url: Optional[str] = None
    attempt_count: int = 0


class MessageResult(BaseModel):
    contact_id: int
    platform: PlatformType
    account_id: int
    status: MessageStatus
    error_message: Optional[str] = None
    sent_at: Optional[datetime] = None
