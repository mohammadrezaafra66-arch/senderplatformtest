from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from core_engine.models import ChannelSession, SessionType


def assign_proxy_to_account(
    db: Session,
    account_id: int,
    proxy_host: str,
    proxy_port: int,
    proxy_username: str | None = None,
    proxy_password: str | None = None,
    proxy_protocol: str = "http",
    pool_id: str | None = None,
    force: bool = False,
) -> ChannelSession:
    channel_session = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )
    if not channel_session:
        channel_session = ChannelSession(
            account_id=account_id,
            session_type=SessionType.EVOLUTION_INSTANCE,
        )
        db.add(channel_session)

    if (
        channel_session.proxy_host
        and channel_session.proxy_host != proxy_host
        and not force
    ):
        raise ValueError(
            f"اکانت {account_id} از قبل proxy دارد. برای تغییر force=True ارسال کنید."
        )

    channel_session.proxy_host = proxy_host
    channel_session.proxy_port = proxy_port
    channel_session.proxy_protocol = proxy_protocol
    channel_session.proxy_username = proxy_username
    if proxy_password:
        channel_session.proxy_password_ciphertext = proxy_password
    channel_session.proxy_pool_id = pool_id
    channel_session.proxy_assigned_at = datetime.utcnow()

    db.commit()
    return channel_session


def get_proxy_config_for_instance(db: Session, account_id: int) -> dict | None:
    channel_session = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )
    if not channel_session or not channel_session.proxy_host:
        return None

    return {
        "host": channel_session.proxy_host,
        "port": str(channel_session.proxy_port or ""),
        "protocol": channel_session.proxy_protocol or "http",
        "username": channel_session.proxy_username,
        "password": channel_session.proxy_password_ciphertext,
    }


def has_proxy_assigned(db: Session, account_id: int) -> bool:
    channel_session = (
        db.query(ChannelSession)
        .filter(
            ChannelSession.account_id == account_id,
            ChannelSession.session_type == SessionType.EVOLUTION_INSTANCE,
        )
        .first()
    )
    return bool(channel_session and channel_session.proxy_host)
