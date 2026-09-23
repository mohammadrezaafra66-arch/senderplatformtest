"""Refill must not wait forever on a campaign row the caller already holds."""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from core_engine.models import Campaign, CampaignStatus, PlatformType
from core_engine.services import queue_bridge


def test_bridge_refill_uses_a_lock_timeout():
    source = open(queue_bridge.__file__, encoding="utf-8").read()
    assert "SET lock_timeout = '2s'" in source


def test_for_update_times_out_while_caller_holds_campaign_row(pg_session_factory):
    holder = pg_session_factory()
    campaign = Campaign(
        name=f"lock-{uuid.uuid4().hex[:8]}",
        channel="rubika",
        title="lock",
        platform=PlatformType.RUBIKA,
        status=CampaignStatus.PREPARED.value,
        template_text="متن",
        use_gpt=False,
        include_products=False,
    )
    holder.add(campaign)
    holder.commit()
    campaign_id = int(campaign.id)

    writer = pg_session_factory()
    waiter = pg_session_factory()
    try:
        row = writer.query(Campaign).filter(Campaign.id == campaign_id).one()
        row.status = CampaignStatus.RUNNING.value
        writer.flush()

        waiter.execute(text("SET lock_timeout = '1s'"))
        started = time.monotonic()
        with pytest.raises(OperationalError):
            waiter.query(Campaign).filter(Campaign.id == campaign_id).with_for_update().one()
        assert time.monotonic() - started < 5
        assert writer.query(Campaign).filter(Campaign.id == campaign_id).one().status == (
            CampaignStatus.RUNNING.value
        )
    finally:
        writer.rollback()
        waiter.close()
        writer.close()
        holder.query(Campaign).filter(Campaign.id == campaign_id).delete()
        holder.commit()
        holder.close()
