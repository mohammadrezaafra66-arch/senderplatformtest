"""تشخیص خام add_address_book — برای فهمیدن اسم واقعی فیلد guid در پاسخ.

اجرا:
    docker compose exec core_api python -m scripts.rubika_user_resolve_guid_debug <account_id> <phone>

این اسکریپت چیزی در DB نمی‌سازد و چیزی پاک نمی‌کند — فقط می‌خواند و چاپ می‌کند.
"""
from __future__ import annotations

import asyncio
import json
import sys

from workers.connectors.rubika_user import _connect_authenticated, load_rubika_user_client


async def main() -> int:
    if len(sys.argv) < 3:
        print("usage: rubika_user_resolve_guid_debug.py <account_id> <phone>")
        return 2

    account_id = int(sys.argv[1])
    phone = sys.argv[2].strip()
    if phone.startswith("0"):
        phone_e164 = f"98{phone[1:]}"
    else:
        phone_e164 = phone

    client = await load_rubika_user_client(account_id)
    await _connect_authenticated(client)
    try:
        print(f"client.guid (خود اکانت فرستنده) = {client.guid}")
        print(f"phone={phone} -> phone_e164={phone_e164}")
        print("=" * 60)

        print("--- add_address_book خام ---")
        result = await client.add_address_book(
            phone=phone_e164, first_name="تست", last_name="دیباگ"
        )
        print(json.dumps(result.to_dict, indent=2, ensure_ascii=False, default=str))
        print("=" * 60)

        print("--- تلاش استخراج با چند اسم احتمالی ---")
        for key in ("user_guid", "guid", "id", "updated_contact"):
            value = getattr(result, key, None)
            print(f"result.{key} = {value!r}")

        print("=" * 60)
        print("--- get_contacts (شاید مخاطب تازه‌اضافه‌شده اینجا با guid کامل دیده شود) ---")
        contacts = await client.get_contacts()
        print(json.dumps(contacts.to_dict, indent=2, ensure_ascii=False, default=str)[:3000])

        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
