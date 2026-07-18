from __future__ import annotations
from datetime import datetime
from sqlalchemy.orm import Session
from core_engine.models import ChannelSession, SessionType


def assign_proxy_to_account(db,account_id,proxy_host,proxy_port,proxy_username=None,proxy_password=None,proxy_protocol="http",pool_id=None,force=False):
    cs=db.query(ChannelSession).filter(ChannelSession.account_id==account_id,ChannelSession.session_type==SessionType.EVOLUTION_INSTANCE).first()
    if not cs:
        cs=ChannelSession(account_id=account_id,session_type=SessionType.EVOLUTION_INSTANCE)
        db.add(cs)
    if cs.proxy_host and cs.proxy_host!=proxy_host and not force:
        raise ValueError(f"اکانت {account_id} از قبل proxy دارد.")
    cs.proxy_host=proxy_host;cs.proxy_port=proxy_port;cs.proxy_protocol=proxy_protocol;cs.proxy_username=proxy_username
    if proxy_password:cs.proxy_password_ciphertext=proxy_password
    cs.proxy_pool_id=pool_id;cs.proxy_assigned_at=datetime.utcnow()
    db.commit();return cs

def get_proxy_config_for_instance(db,account_id):
    cs=db.query(ChannelSession).filter(ChannelSession.account_id==account_id,ChannelSession.session_type==SessionType.EVOLUTION_INSTANCE).first()
    if not cs or not cs.proxy_host:return None
    return {"host":cs.proxy_host,"port":str(cs.proxy_port or ""),"protocol":cs.proxy_protocol or "http","username":cs.proxy_username,"password":cs.proxy_password_ciphertext}

def has_proxy_assigned(db,account_id):
    cs=db.query(ChannelSession).filter(ChannelSession.account_id==account_id,ChannelSession.session_type==SessionType.EVOLUTION_INSTANCE).first()
    return bool(cs and cs.proxy_host)
