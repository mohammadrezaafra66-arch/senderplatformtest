import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from core_engine.database import Base
from core_engine.models import (
    Campaign,
    CampaignRecipient,
    CampaignStatus,
    Contact,
    PlatformType,
    SendStatus,
)


def _postgres_url() -> str | None:
    url = os.getenv("DATABASE_URL")
    return url if url and url.startswith("postgresql") else None


@pytest.fixture
def pg_engine():
    url = _postgres_url()
    if not url:
        pytest.skip("DATABASE_URL not set for worker DB tests")
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        pytest.skip("Postgres not reachable for worker DB tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)


@pytest.fixture
def recipient_bundle(pg_session_factory):
    session: Session = pg_session_factory()
    campaign = Campaign(
        name="Worker DB Test",
        title="Worker DB Test",
        channel="bale",
        platform=PlatformType.BALE,
        status=CampaignStatus.RUNNING.value,
        template_text="Hi",
    )
    session.add(campaign)
    session.flush()

    contact = Contact(phone="+989120000099", first_name="Worker")
    session.add(contact)
    session.flush()

    recipient = CampaignRecipient(
        campaign_id=campaign.id,
        contact_id=contact.id,
        send_status=SendStatus.QUEUED,
    )
    session.add(recipient)
    session.commit()

    yield campaign.id, contact.id, session

    session.query(CampaignRecipient).filter(CampaignRecipient.campaign_id == campaign.id).delete()
    session.query(Contact).filter(Contact.id == contact.id).delete()
    session.query(Campaign).filter(Campaign.id == campaign.id).delete()
    session.commit()
    session.close()
