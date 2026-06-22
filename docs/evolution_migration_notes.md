# یادداشت‌های مهاجرت Evolution API

## وضعیت کلی
- تاریخ شروع فاز ۰: <TODO>
- مسئول اجرا: <TODO>

## مشخصات VPS میزبان (مجزا از Proxy per-instance!)
- ارائه‌دهنده و پلن: <TODO>
- IP عمومی: <TODO>
- موقعیت دیتاسنتر: <TODO>
- مشخصات سخت‌افزاری (RAM/CPU/Disk): <TODO>
- سیستم‌عامل: <TODO>
- روش دسترسی (SSH key fingerprint): <TODO>
- هزینه‌ی ماهانه: <TODO>
- تاریخ راه‌اندازی: <TODO>

## فهرست ارائه‌دهندگان Proxy ارزیابی‌شده
| ارائه‌دهنده | Sticky Session؟ | پروتکل خام host:port:user:pass؟ | نوع IP | نتیجه |
|---|---|---|---|---|
| <TODO> | <TODO> | <TODO> | <TODO> | <TODO> |

⚠ هشدار ثابت: سرویس‌های Web Scraping API (مثل Decodo) برای این کاربرد مناسب نیستند، چون
IP را per-request می‌چرخانند و پروتکل خام proxy نمی‌دهند. به فاز ۰.۳ سند اصلی رجوع کن.

## نتیجه‌ی تست Sticky بودن Proxy اکانت Pilot
- اکانت Pilot: <TODO>
- IP ثابت تأییدشده؟ <TODO>
- مدت تست: <TODO>

## لاگ تغییرات این فاز
- <TODO>

## اسکریپت‌های کمکی فاز ۰ (خارج از scope تسک‌های شماره‌گذاری‌شده)

### `scripts/create_evolution_db.sh`
- **چه می‌کند:** از ریشهٔ repo اجرا می‌شود؛ به `multi-messaging-platform` می‌رود و با `docker compose exec postgres` دیتابیس `evolution_db` را روی همان Postgres موجود پروژه می‌سازد (idempotent با `WHERE NOT EXISTS`) و `GRANT ALL` به `mmp_user` می‌دهد.
- **چرا لازم بود:** سرویس `evolution_api` در `docker-compose.yml` به `evolution_db` وصل است؛ mount خودکار `postgres-init` حذف شده و Evolution schema را خود image می‌سازد — فقط **خود دیتابیس** باید یک‌بار قبل از اولین `docker compose up evolution_api` وجود داشته باشد.
- **scope:** مانند `setup_vps.sh` — **خارج از scope تسک‌های شماره‌گذاری‌شده**؛ افزودهٔ جانبی در پرامپت پیاده‌سازی فاز ۰ (bootstrap DB). نگه داشته می‌شود چون بدون آن Evolution روی Postgres مشترک بالا نمی‌آید.

## TODO فاز ۳ — Webhook FastAPI
- مسیر webhook Evolution باید **عیناً** در روتر FastAPI ثبت شود:
  `POST /webhooks/whatsapp/evolution`
  (مقدار `EVOLUTION_WEBHOOK_URL` و `WEBHOOK_GLOBAL_URL` در docker-compose همین مسیر را دارد.)
