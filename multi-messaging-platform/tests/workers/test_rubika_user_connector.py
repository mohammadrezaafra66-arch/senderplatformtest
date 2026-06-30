import base64
import json

import pytest
from Crypto.Cipher import PKCS1_OAEP
from Crypto.PublicKey import RSA
from cryptography.fernet import Fernet

from core_engine.config import get_settings
from core_engine.models import (
    Account,
    AccountStatus,
    Campaign,
    ChannelSession,
    Contact,
    PlatformType,
    RubikaAccountPool,
    RubikaGlobalSentRegistry,
    RubikaSenderSchedule,
    SessionType,
)
from core_engine.services.rubika_user_session import (
    build_session_envelope,
    parse_session_envelope,
    start_rubika_user_login,
    verify_rubika_user_login,
)
from core_engine.services.session_storage import store_channel_session
from rubpy.types import Update
from workers.config import WorkerSettings
from workers.connectors.rubika_user import deliver_rubika_user_live, load_rubika_user_client
from workers.errors import SessionInvalidError
from workers.payloads import WorkerPayload
from workers.rubika_account_pool import RubikaAccountPoolManager, resolve_current_phase


def _live_settings(**overrides) -> WorkerSettings:
    base = dict(
        DRY_RUN=False,
        SHADOW_MODE=False,
        REAL_MESSAGE_SENDING_ENABLED=True,
        CHANNEL_CONNECTORS_ENABLED=True,
        RUBIKA_DELIVERY_MODE="user_account",
        RUBIKA_USER_ACCOUNT_ENABLED=True,
        RUBIKA_HOURLY_SEND_CAP=50,
        RUBIKA_MIN_SEND_DELAY_SECONDS=1,
        RUBIKA_MAX_SEND_DELAY_SECONDS=2,
    )
    base.update(overrides)
    return WorkerSettings(**base)


@pytest.fixture(autouse=True)
def worker_session_secret(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SESSION_SECRET", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """core_engine.services.redis_client کلاینت را module-level کش می‌کند؛ چون

    pytest-asyncio برای هر تست یک event loop تازه می‌سازد، اتصال کش‌شده تست قبلی
    به loop بسته‌شده گره می‌خورد. قبل/بعد هر تست بازنشانی می‌کنیم (دقیقاً همان
    کاری که reset_redis_client برای همین منظور در آن ماژول وجود دارد).
    """
    from core_engine.services.redis_client import reset_redis_client

    reset_redis_client()
    yield
    reset_redis_client()


# --------------------------------------------------------------------------
# envelope — pure functions، بدون DB/شبکه
# --------------------------------------------------------------------------


def test_build_and_parse_session_envelope_roundtrip():
    envelope = build_session_envelope(
        phone_number="989120000000",
        auth="a" * 32,
        guid="uTEST",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
    )
    parsed = parse_session_envelope(envelope)
    assert parsed["guid"] == "uTEST"
    assert parsed["auth"] == "a" * 32


def test_parse_session_envelope_missing_field_raises():
    with pytest.raises(ValueError):
        parse_session_envelope(json.dumps({"phone_number": "x"}))


# --------------------------------------------------------------------------
# جریان OTP — DB واقعی (postgres)، شبکه rubpy mock شده
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rubika_user_login_flow_success(monkeypatch, pg_session_factory):
    session = pg_session_factory()
    leftover_ids = [
        row.id
        for row in session.query(Account).filter(Account.label == "otp-test-account").all()
    ]
    if leftover_ids:
        session.query(ChannelSession).filter(ChannelSession.account_id.in_(leftover_ids)).delete(
            synchronize_session=False
        )
        session.query(Account).filter(Account.id.in_(leftover_ids)).delete(
            synchronize_session=False
        )
        session.commit()

    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number="989121110011",
        label="otp-test-account",
        status=AccountStatus.REQUIRES_LOGIN,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    fake_send_code_result = Update({"status": "OK", "phone_code_hash": "hash123"})

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def mock_send_code(self, **kwargs):
        return fake_send_code_result

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.send_code", mock_send_code)

    start_result = await start_rubika_user_login(
        account_id=account.id, phone_number="09121110011"
    )
    assert start_result["stage"] == "code_required"
    registration_token = start_result["registration_token"]

    from core_engine.services.redis_client import get_redis_client

    redis = get_redis_client()
    raw_state = await redis.get(f"rubika:user_login:{registration_token}")
    state = json.loads(raw_state)
    public_key = state["public_key"]

    from rubpy.crypto import Crypto as RubikaCrypto

    raw_pub_b64 = RubikaCrypto.decode_auth(public_key)
    pub_key_obj = RSA.import_key(base64.b64decode(raw_pub_b64))
    encrypted_auth = base64.b64encode(
        PKCS1_OAEP.new(pub_key_obj).encrypt(b"b" * 32)
    ).decode()

    fake_sign_in_result = Update(
        {
            "status": "OK",
            "auth": encrypted_auth,
            "user": {"user_guid": "uOTPTEST", "phone": "989121110011"},
        }
    )

    async def fake_sign_in(self, **kwargs):
        return fake_sign_in_result

    async def fake_register_device(self, **kwargs):
        return None

    monkeypatch.setattr("rubpy.Client.sign_in", fake_sign_in)
    monkeypatch.setattr("rubpy.Client.register_device", fake_register_device)

    verify_result = await verify_rubika_user_login(
        session, registration_token=registration_token, phone_code="11111"
    )
    assert verify_result["success"] is True
    assert verify_result["guid"] == "uOTPTEST"

    session.refresh(account)
    assert account.status == AccountStatus.ACTIVE

    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------


def _make_send_ready_account(session, *, label: str, phase: str) -> Account:
    account = Account(
        platform=PlatformType.RUBIKA,
        phone_number=f"98912{label[-6:]}",
        label=label,
        status=AccountStatus.ACTIVE,
    )
    session.add(account)
    session.flush()

    envelope = build_session_envelope(
        phone_number=account.phone_number,
        auth="c" * 32,
        guid=f"uSENDER-{label}",
        user_agent="ua",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----",
    )
    store_channel_session(
        session, account_id=account.id, session_type=SessionType.RUBIKA_SESSION,
        plaintext=envelope,
    )
    session.add(RubikaAccountPool(account_id=account.id, phase=phase, priority=1))
    session.commit()
    return account


@pytest.mark.asyncio
async def test_deliver_rubika_user_live_missing_session(pg_session_factory):
    session = pg_session_factory()
    with pytest.raises(SessionInvalidError):
        await load_rubika_user_client(999999, db=session)
    session.close()


@pytest.mark.asyncio
async def test_deliver_rubika_user_live_success_and_then_duplicate(monkeypatch, pg_session_factory):
    session = pg_session_factory()

    # پاکسازی باقیمانده اجراهای ناقص قبلی (idempotent rerun)
    leftover_contact_ids = [
        row.id for row in session.query(Contact).filter(Contact.phone_e164 == "989120009999").all()
    ]
    if leftover_contact_ids:
        session.query(RubikaGlobalSentRegistry).filter(
            RubikaGlobalSentRegistry.contact_id.in_(leftover_contact_ids)
        ).delete(synchronize_session=False)
        session.query(Contact).filter(Contact.id.in_(leftover_contact_ids)).delete(
            synchronize_session=False
        )
    leftover_account_ids = [
        row.id for row in session.query(Account).filter(Account.label == "senduser1").all()
    ]
    if leftover_account_ids:
        session.query(ChannelSession).filter(
            ChannelSession.account_id.in_(leftover_account_ids)
        ).delete(synchronize_session=False)
        session.query(RubikaAccountPool).filter(
            RubikaAccountPool.account_id.in_(leftover_account_ids)
        ).delete(synchronize_session=False)
        session.query(Account).filter(Account.id.in_(leftover_account_ids)).delete(
            synchronize_session=False
        )
    session.query(Campaign).filter(Campaign.title == "user-connector-test").delete(
        synchronize_session=False
    )
    session.commit()

    # اطمینان از این‌که حداقل یک schedule فعال هست که فاز جاری را پوشش بدهد —
    # تست را مستقل از زمان واقعی اجرا می‌کنیم با یک بازه ۰ تا ۲۴ (همیشه فعال).
    session.query(RubikaSenderSchedule).delete()
    session.add(
        RubikaSenderSchedule(phase="day", start_hour=0, end_hour=24, max_per_hour=999, is_active=True)
    )
    session.commit()

    account = _make_send_ready_account(session, label="senduser1", phase="day")

    campaign = Campaign(
        name="user-connector-test", title="user-connector-test", channel="rubika",
        platform=PlatformType.RUBIKA,
    )
    session.add(campaign)
    session.flush()

    contact = Contact(campaign_id=campaign.id, first_name="تست", phone="09120009999",
                       phone_e164="989120009999")
    session.add(contact)
    session.commit()

    payload = WorkerPayload(
        message_id=1, campaign_id=campaign.id, contact_id=contact.id,
        account_id=account.id, platform="rubika", recipient="989120009999",
        recipient_type="phone", message_text="سلام تستی",
        dedupe_key="dedupe-user-1",
    )
    settings = _live_settings()

    fake_addr = Update({"user_guid": "uRESOLVEDCONTACT"})
    fake_send = Update({"message_id": "555"})

    async def fake_connect(self):
        return None

    async def fake_disconnect(self):
        return None

    async def fake_add_address_book(self, **kwargs):
        return fake_addr

    async def fake_send_message(self, **kwargs):
        return fake_send

    monkeypatch.setattr("rubpy.Client.connect", fake_connect)
    monkeypatch.setattr("rubpy.Client.disconnect", fake_disconnect)
    monkeypatch.setattr("rubpy.Client.add_address_book", fake_add_address_book)
    monkeypatch.setattr("rubpy.Client.send_message", fake_send_message)

    result = await deliver_rubika_user_live(payload, settings, db=session)
    assert result.success is True
    assert result.status == "delivered"
    assert result.platform_message_id == "rubika-user-555"

    session.refresh(contact)
    assert contact.extra_variables.get("rubika_guid") == "uRESOLVEDCONTACT"

    dup = (
        session.query(RubikaGlobalSentRegistry)
        .filter(RubikaGlobalSentRegistry.contact_id == contact.id)
        .first()
    )
    assert dup is not None

    # فراخوانی دوم — باید بدون لمس شبکه رد شود
    async def fail_if_called(self, **kwargs):
        raise AssertionError("نباید برای contact تکراری دوباره فراخوانی شود")

    monkeypatch.setattr("rubpy.Client.add_address_book", fail_if_called)
    monkeypatch.setattr("rubpy.Client.send_message", fail_if_called)

    payload2 = payload.model_copy(update={"message_id": 2, "dedupe_key": "dedupe-user-2"})
    result2 = await deliver_rubika_user_live(payload2, settings, db=session)
    assert result2.success is True
    assert result2.status == "skipped_duplicate"

    # پاکسازی
    session.query(RubikaGlobalSentRegistry).filter(
        RubikaGlobalSentRegistry.contact_id == contact.id
    ).delete()
    session.query(RubikaAccountPool).filter(RubikaAccountPool.account_id == account.id).delete()
    session.query(ChannelSession).filter(ChannelSession.account_id == account.id).delete()
    session.query(Contact).filter(Contact.id == contact.id).delete()
    session.query(Campaign).filter(Campaign.id == campaign.id).delete()
    session.query(Account).filter(Account.id == account.id).delete()
    session.commit()
    session.close()


@pytest.mark.asyncio
async def test_resolve_current_phase_none_outside_any_window(pg_session_factory):
    session = pg_session_factory()
    session.query(RubikaSenderSchedule).delete()
    session.commit()
    assert resolve_current_phase(session) is None
    session.close()
