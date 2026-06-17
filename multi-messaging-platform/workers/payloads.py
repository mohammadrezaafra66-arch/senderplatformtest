"""ساختار payload و نتیجه Worker."""

from typing import Any

from pydantic import BaseModel, Field


class WorkerPayload(BaseModel):
    message_id: int | str
    campaign_id: int | str
    contact_id: int | str
    account_id: int | str
    platform: str
    recipient: str
    recipient_type: str
    message_text: str
    media_url: str | None = None
    dedupe_key: str
    attempt: int = 1
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    success: bool
    status: str
    error_code: str | None = None
    error_message: str | None = None
    platform_message_id: str | None = None
    retryable: bool = False
