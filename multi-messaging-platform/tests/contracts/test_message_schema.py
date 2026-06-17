import pytest
from pydantic import ValidationError

from core_engine.schemas.message import MessagePayload, MessageResult


VALID_PAYLOAD = {
    "contact_id": 1,
    "campaign_id": 10,
    "platform": "whatsapp",
    "chat_identifier": "+989123456789",
    "message_text": "Hello from contract test",
    "media_url": None,
    "attempt_count": 0,
}


def test_message_payload_valid_instance():
    payload = MessagePayload(**VALID_PAYLOAD)
    assert payload.contact_id == 1
    assert payload.campaign_id == 10
    assert payload.platform == "whatsapp"
    assert payload.chat_identifier == "+989123456789"
    assert payload.message_text == "Hello from contract test"
    assert payload.attempt_count == 0


@pytest.mark.parametrize("missing_field", ["contact_id", "campaign_id", "chat_identifier"])
def test_message_payload_missing_required_field_raises(missing_field):
    data = {k: v for k, v in VALID_PAYLOAD.items() if k != missing_field}
    with pytest.raises(ValidationError):
        MessagePayload(**data)


def test_message_payload_invalid_platform_raises():
    data = {**VALID_PAYLOAD, "platform": "email"}
    with pytest.raises(ValidationError):
        MessagePayload(**data)


def test_message_payload_non_numeric_attempt_count_raises():
    data = {**VALID_PAYLOAD, "attempt_count": "not-a-number"}
    with pytest.raises(ValidationError):
        MessagePayload(**data)


VALID_RESULT = {
    "contact_id": 1,
    "platform": "telegram",
    "account_id": 5,
    "status": "success",
}


def test_message_result_valid_instance():
    result = MessageResult(**VALID_RESULT)
    assert result.contact_id == 1
    assert result.platform == "telegram"
    assert result.account_id == 5
    assert result.status == "success"
    assert result.error_message is None
    assert result.sent_at is None


def test_message_result_invalid_status_raises():
    data = {**VALID_RESULT, "status": "pending"}
    with pytest.raises(ValidationError):
        MessageResult(**data)
