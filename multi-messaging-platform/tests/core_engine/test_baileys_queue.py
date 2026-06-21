"""Tests for Baileys Redis bridge (Python producer side)."""

from __future__ import annotations

import json

import pytest

from core_engine.services.baileys_queue import (
    RAW_OUTGOING_LIST,
    build_baileys_job,
    enqueue_baileys_job,
)


@pytest.mark.asyncio
async def test_enqueue_baileys_job_rpush(monkeypatch):
    from unittest.mock import AsyncMock

    mock_client = AsyncMock()
    monkeypatch.setattr(
        "core_engine.services.redis_client.get_redis_client",
        lambda: mock_client,
    )

    job = build_baileys_job(
        job_id="job-1",
        sender_phone="989048249523",
        recipient="989122270261",
        text="hello",
        route="ui",
    )

    await enqueue_baileys_job(job)

    mock_client.rpush.assert_awaited_once()
    args = mock_client.rpush.await_args.args
    assert args[0] == RAW_OUTGOING_LIST
    payload = json.loads(args[1])
    assert payload["jobId"] == "job-1"
    assert payload["accountId"] == "989048249523"
    assert payload["text"] == "hello"
