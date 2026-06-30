# Rubika v2 — Phase 1+2 Test Report

Branch: `feature/rubika-module`
Summary: **end-to-end تأیید شد روی اکانت واقعی روبیکا (account_id=282)**

## فاز ۱ — DB

- ✅ **alembic upgrade head** — ۱۲ migration (۱۱ موجود + جدید) بدون خطا، Postgres 16 و Postgres 15 (واقعی production در docker-compose)
- ✅ **۵ جدول جدید** — `rubika_account_pool`, `rubika_allowed_groups`, `rubika_global_sent_registry`, `rubika_group_messages`, `rubika_sender_schedules`
- ✅ رفع gap قدیمی: `EVOLUTION_INSTANCE` هیچ‌وقت در enum واقعی Postgres نبود — این migration هر دو (`EVOLUTION_INSTANCE` و `RUBIKA_SESSION`) را اضافه کرد

## فاز ۲ — OTP login + ارسال user_account

- ✅ **OTP login واقعی** — `POST /accounts/{id}/rubika/session/register` → کد پیامکی واقعی → `POST .../verify` → `guid` واقعی (`u0IRJ570093c24532e26b0ea5e44e5b3`), `session_registered=true`, `ready_for_delivery=true`
- ✅ **ارسال متن واقعی** — رسید روی گوشی گیرنده (`09903858654`)
- ✅ **ارسال عکس+کپشن واقعی** — رسید روی گوشی گیرنده
- ✅ **dedup سراسری** — فراخوانی دوم به همان contact → `skipped_duplicate` بدون لمس شبکه
- ✅ **pytest** — ۶/۶، با داده واقعی پاسخ rubpy (نه mock حدسی)، idempotent در اجرای مکرر

## باگ‌های واقعی پیدا و رفع‌شده (فقط با تست روی اکانت واقعی قابل کشف بودند)

| # | باگ | کجا | رفع |
|---|---|---|---|
| ۱ | `rubpy.sessions.StringSession.insert()` مقدار `private_key` را گم می‌کند | rubpy ۷.۳.۵ (کتابخانه ثالث) | envelope را خودمان ۵‌عنصری دستی می‌سازیم |
| ۲ | `rubpy.exceptions` با `__getattr__` پویا — دسترسی PascalCase بی‌صدا کلاس اشتباه برمی‌گرداند | rubpy ۷.۳.۵ | فقط snake_case (`exceptions.not_registered`) |
| ۳ | `Client.connect()` فقط `auth`/`guid`/`private_key` را می‌خواند، `import_key` را نه → `AttributeError: NoneType has no attribute 'sign'` در هر درخواست امضادار | rubpy ۷.۳.۵ | `_connect_authenticated()` (رفع‌شده توسط نشست موازی Claude، commit `c96a7c6`) |
| ۴ | `rubpy.types.Update.find_keys()` فقط روی اولین فرزند dict سطح بالا recurse می‌کند، بقیه را نه — `result.user_guid` همیشه `None` با اینکه `result["user"]["user_guid"]` مقدار دارد | rubpy ۷.۳.۵ | `_deep_find()` — جستجوی recursive درست در `workers/connectors/rubika_user.py` |
| ۵ | `MediaThumbnail.from_image()` بدون Pillow نصب‌شده `None` برمی‌گرداند → پیام عکس بدون `thumb_inline`/ابعاد واقعی به سرور می‌رود → `ERROR_GENERIC`/`INVALID_INPUT` | Docker image (Pillow نبود) | افزودن `Pillow` به `requirements.txt` |

## محیط دستی استفاده‌شده برای تست (نه پروداکشن)

- لپ‌تاپ ویندوزی پورچیستا، `docker compose up -d postgres redis core_api`
- `core_api` روی پورت میزبان **8001** (نه 8000 — اشغال توسط استک Lovable/Supabase دیگری که روی همین سیستم اجراست)
- اکانت تست واقعی: `account_id=282`، در pool فاز `day` — **برای تست‌های بعدی نگه داشته شده، پاک نشود**

## معماری — تغییر نسبت به سند اولیه (`سند اجرایی نهایی — ماژول روبیکا`)

فاز ۳ سند (Celery task برای enforce کردن پنجره روز/شب + reset شمارش ساعتی) دیگر لازم نیست:
`resolve_current_phase()` زنده از `rubika_sender_schedules` می‌خواند (نه از env کش‌شده)، و سقف ساعتی/min-delay از Redis TTL خودش مدیریت می‌شود (`workers/rate_limit.py`، همان مکانیزم WhatsApp).

## باقی‌مانده (فازهای بعدی سند)

- فاز ۴ — API/Frontend کامل (الان مدیریت pool و session دستی/SQL است)
- فاز ۵ — پایش گروه‌ها
- فاز ۶ — ویس + عکس گروه
- فاز ۷ — هوش مصنوعی
- فاز ۸ — پاکسازی + استاتوس
