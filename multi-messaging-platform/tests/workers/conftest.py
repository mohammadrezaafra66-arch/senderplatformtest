import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from core_engine.database import Base
from tests.isolation import (
    assert_connected_pytest_database,
    assert_pytest_database_url,
    truncate_pytest_database,
)
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
    assert_pytest_database_url(url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            assert_connected_pytest_database(conn)
    except pytest.skip.Exception:
        raise
    except RuntimeError:
        engine.dispose()
        raise
    except Exception:
        engine.dispose()
        pytest.skip("Postgres not reachable for worker DB tests")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    with pg_engine.connect() as conn:
        assert_connected_pytest_database(conn)
    Base.metadata.create_all(pg_engine)
    maker = sessionmaker(autocommit=False, autoflush=False, bind=pg_engine)
    opened: list = []

    def factory():
        session = maker()
        opened.append(session)
        return session

    yield factory
    for session in opened:
        session.close()
    truncate_pytest_database(pg_engine)


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
