import pytest
from unittest.mock import MagicMock
from workers.connectors.telegram_mtproto import (
    deliver_telegram_mtproto_live,
    _check_account_pool,
    _increment_sent_count,
)
from workers.payloads import WorkerPayload
from workers.config import WorkerSettings


def _mtproto_settings() -> WorkerSettings:
    return WorkerSettings(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        TELEGRAM_DELIVERY_MODE="mtproto_account",
        TELEGRAM_ENABLE_MTPROTO=True,
        TELEGRAM_API_ID="123",
        TELEGRAM_API_HASH="abc",
        TELEGRAM_MIN_SEND_DELAY_SECONDS=0,
        TELEGRAM_MAX_SEND_DELAY_SECONDS=0,
        TELEGRAM_MTPROTO_SESSION_DIR="/tmp/tg_test_sessions",
    )


def _sample_payload(**overrides) -> WorkerPayload:
    base = dict(
        message_id=1, campaign_id=10, contact_id=20, account_id=1,
        platform="telegram", recipient="989121234567",
        recipient_type="phone_number", message_text="سلام تست",
        dedupe_key="test-1",
    )
    base.update(overrides)
    return WorkerPayload.model_validate(base)


@pytest.mark.asyncio
async def test_mtproto_skips_duplicate(monkeypatch):
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = MagicMock()
    monkeypatch.setattr("workers.connectors.telegram_mtproto.get_db_session", lambda: mock_db)
    result = await deliver_telegram_mtproto_live(_sample_payload(), _mtproto_settings())
    assert result.success is False
    assert result.error_code == "telegram_already_sent"
    assert result.status == "skipped_duplicate"


@pytest.mark.asyncio
async def test_mtproto_blocked_by_daily_cap(monkeypatch):
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr("workers.connectors.telegram_mtproto.get_db_session", lambda: mock_db)
    monkeypatch.setattr(
        "workers.connectors.telegram_mtproto._check_account_pool",
        lambda _: {"allowed": False, "reason": "daily_cap_reached"},
    )
    result = await deliver_telegram_mtproto_live(_sample_payload(), _mtproto_settings())
    assert result.success is False
    assert result.error_code == "telegram_pool_daily_cap_reached"
    assert result.retryable is True


@pytest.mark.asyncio
async def test_mtproto_disabled_gate(monkeypatch):
    settings = _mtproto_settings()
    settings.TELEGRAM_ENABLE_MTPROTO = False
    from workers.delivery import deliver_platform_message
    result = await deliver_platform_message("telegram", _sample_payload(), settings)
    assert result.error_code == "telegram_mtproto_disabled"


def test_calculate_daily_cap_day_zero():
    from core_engine.services.telegram_warmup import calculate_daily_cap
    cap = calculate_daily_cap(0)
    assert cap == 10


def test_calculate_daily_cap_after_warmup():
    from core_engine.services.telegram_warmup import calculate_daily_cap
    cap = calculate_daily_cap(14)
    assert cap == 80


def test_calculate_daily_cap_mid_warmup():
    from core_engine.services.telegram_warmup import calculate_daily_cap
    cap = calculate_daily_cap(7)
    assert 10 < cap < 80


@pytest.mark.asyncio
async def test_verify_without_start_returns_error():
    from core_engine.services.telegram_session_setup import verify_phone_code
    db = MagicMock()
    result = await verify_phone_code(db, account_id=999, phone_number="+989000", code="12345")
    assert result["status"] == "error"
