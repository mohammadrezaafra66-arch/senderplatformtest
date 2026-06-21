"""Tests for WhatsApp warmup trigger service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from core_engine.services.whatsapp_warmup import (
    WARMUP_LOCK_KEY,
    WARMUP_LOCK_TTL_SECONDS,
    trigger_whatsapp_warmup,
)


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


@pytest.mark.asyncio
async def test_trigger_warmup_success(mock_redis):
    response = MagicMock()
    response.is_success = True
    response.json.return_value = {
        "success": True,
        "message": "Warmup scheduled",
        "pairedAccounts": 2,
        "totalJobs": 4,
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "core_engine.services.whatsapp_warmup.get_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "core_engine.services.whatsapp_warmup.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        result = await trigger_whatsapp_warmup()

    assert result["pairedAccounts"] == 2
    assert result["totalJobs"] == 4
    mock_redis.setex.assert_awaited_once_with(
        WARMUP_LOCK_KEY,
        WARMUP_LOCK_TTL_SECONDS,
        "locked",
    )
    mock_redis.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_warmup_returns_429_when_lock_exists(mock_redis):
    mock_redis.exists = AsyncMock(return_value=1)

    with patch(
        "core_engine.services.whatsapp_warmup.get_redis_client",
        return_value=mock_redis,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_whatsapp_warmup()

    assert exc_info.value.status_code == 429
    mock_redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_warmup_rollbacks_lock_on_node_400(mock_redis):
    response = MagicMock()
    response.is_success = False
    response.status_code = 400
    response.json.return_value = {
        "success": False,
        "error": "insufficient_accounts",
        "pairedAccounts": 1,
    }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "core_engine.services.whatsapp_warmup.get_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "core_engine.services.whatsapp_warmup.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_whatsapp_warmup()

    assert exc_info.value.status_code == 400
    mock_redis.delete.assert_awaited_once_with(WARMUP_LOCK_KEY)


@pytest.mark.asyncio
async def test_trigger_warmup_rollbacks_lock_on_connection_error(mock_redis):
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "core_engine.services.whatsapp_warmup.get_redis_client",
            return_value=mock_redis,
        ),
        patch(
            "core_engine.services.whatsapp_warmup.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await trigger_whatsapp_warmup()

    assert exc_info.value.status_code == 503
    mock_redis.delete.assert_awaited_once_with(WARMUP_LOCK_KEY)
