"""One-shot Rubika user-account send test (واتساپ معادلش: test_wa_send_once.py).

پیش‌نیاز: اکانت روبیکا باید از قبل با
POST /accounts/{account_id}/rubika/session/register + /verify لاگین شده باشد
(session_type=RUBIKA_SESSION در channel_sessions).

اجرا:
    docker compose exec core_api python -m scripts.rubika_user_send_test <account_id> <phone> ["متن"] [image_url]

مثال:
    docker compose exec core_api python -m scripts.rubika_user_send_test 7 09121234567 "سلام تستی"
    docker compose exec core_api python -m scripts.rubika_user_send_test 7 09121234567 "با عکس" https://example.com/test.jpg

این اسکریپت یک Campaign + Contact واقعی و موقت می‌سازد (چون connector برای
resolve guid و dedup سراسری به contact_id واقعی نیاز دارد — برخلاف
/accounts/{id}/send-test عمومی که contact_id جعلی می‌سازد و برای این connector
کار نمی‌کند) و در پایان آن‌ها را پاک می‌کند مگر با --keep نگه داشته شوند.
"""
from __future__ import annotations

import asyncio
import sys

from core_engine.database import SessionLocal
from core_engine.models import Account, Campaign, Contact, PlatformType
from workers.config import get_worker_settings
from workers.connectors.rubika_user import deliver_rubika_user_live
from workers.payloads import WorkerPayload


async def main() -> int:
    if len(sys.argv) < 3:
        print("usage: rubika_user_send_test.py <account_id> <phone> [text] [media_url] [--keep]")
        return 2

    account_id = int(sys.argv[1])
    phone = sys.argv[2].strip()
    keep = "--keep" in sys.argv
    args = [a for a in sys.argv[3:] if a != "--keep"]
    text = args[0] if len(args) > 0 else "سلام، این یک پیام تستی از پلتفرم سندر افراکالا است."
    media_url = args[1] if len(args) > 1 else None

    if phone.startswith("0"):
        phone_e164 = f"98{phone[1:]}"
    else:
        phone_e164 = phone

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if account is None:
            print(f"ERROR account {account_id} not found")
            return 2
        if account.platform != PlatformType.RUBIKA:
            print(f"ERROR account {account_id} is not a Rubika account (platform={account.platform})")
            return 2

        print(f"account_id={account_id} label={account.label!r} status={account.status}")
        print(f"phone={phone} -> phone_e164={phone_e164}")
        print(f"text={text!r} media_url={media_url!r}")

        campaign = Campaign(
            name="rubika-user-send-test", title="rubika-user-send-test",
            channel="rubika", platform=PlatformType.RUBIKA,
        )
        db.add(campaign)
        db.flush()

        contact = Contact(
            campaign_id=campaign.id, first_name="تست", phone=phone, phone_e164=phone_e164,
        )
        db.add(contact)
        db.commit()
        db.refresh(contact)
        print(f"contact_id={contact.id} campaign_id={campaign.id}")

        payload = WorkerPayload(
            message_id=1, campaign_id=campaign.id, contact_id=contact.id,
            account_id=account_id, platform="rubika", recipient=phone_e164,
            recipient_type="phone", message_text=text, media_url=media_url,
            dedupe_key=f"manual-test-{contact.id}",
        )
        settings = get_worker_settings()
        print(
            f"RUBIKA_DELIVERY_MODE={settings.RUBIKA_DELIVERY_MODE} "
            f"RUBIKA_USER_ACCOUNT_ENABLED={settings.RUBIKA_USER_ACCOUNT_ENABLED}"
        )

        result = await deliver_rubika_user_live(payload, settings)
        print("RESULT", result.model_dump())

        if not keep:
            from core_engine.models import RubikaGlobalSentRegistry

            db.query(RubikaGlobalSentRegistry).filter(
                RubikaGlobalSentRegistry.contact_id == contact.id
            ).delete()
            db.query(Contact).filter(Contact.id == contact.id).delete()
            db.query(Campaign).filter(Campaign.id == campaign.id).delete()
            db.commit()
            print("cleaned up test campaign/contact (use --keep to retain)")

        return 0 if result.success else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
