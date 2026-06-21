"""Tests for Baileys session disconnect handler."""

from core_engine.models import Account, AccountStatus, ChannelSession, PlatformType
from core_engine.services.baileys_session_service import mark_baileys_session_disconnected


def test_mark_baileys_session_disconnected(pg_session_factory):
    session = pg_session_factory()
    phone = "989999000123"
    account = Account(
        platform=PlatformType.WHATSAPP,
        phone_number=phone,
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    ok = mark_baileys_session_disconnected(session, phone, reason="session_invalid_401")
    assert ok is True
    session.commit()

    session.refresh(account)
    assert account.status == AccountStatus.REQUIRES_LOGIN

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.delete(account)
    session.commit()
    session.close()
