# یادداشت‌های مهاجرت Evolution API

## وضعیت کلی
- تاریخ شروع فاز ۰: در انتظار
- مسئول اجرا: در انتظار

## مشخصات VPS میزبان (مجزا از Proxy per-instance!)
- ارائه‌دهنده: Hetzner Cloud
- پلن: CPX22
- IP عمومی: 23.88.119.119
- موقعیت دیتاسنتر: Falkenstein, Germany (eu-central)
- RAM: 4 GB
- CPU: 2 vCPU (x86)
- Disk: 80 GB
- سیستم‌عامل: Ubuntu 26.04 LTS
- روش دسترسی: SSH با کلید ed25519 (fingerprint: SHA256:4ed7g9o2i0lifnsahuwqQl41RaQ8g9cZEVA1Uh7yqvI)
- هزینه‌ی ماهانه: ~€25.09 (شامل IPv4)
- تاریخ راه‌اندازی: 2026-06-23

## فهرست ارائه‌دهندگان Proxy ارزیابی‌شده
**وضعیت:** در انتظار

| ارائه‌دهنده | Sticky Session؟ | پروتکل خام host:port:user:pass؟ | نوع IP | نتیجه |
|---|---|---|---|---|
| در انتظار | در انتظار | در انتظار | در انتظار | در انتظار |

⚠ هشدار ثابت: سرویس‌های Web Scraping API (مثل Decodo) برای این کاربرد مناسب نیستند، چون
IP را per-request می‌چرخانند و پروتکل خام proxy نمی‌دهند. به فاز ۰.۳ سند اصلی رجوع کن.

## نتیجه‌ی تست Sticky بودن Proxy اکانت Pilot
**وضعیت:** در انتظار

- اکانت Pilot: در انتظار
- IP ثابت تأییدشده؟ در انتظار
- مدت تست: در انتظار

## لاگ تغییرات این فاز
- در انتظار

## اسکریپت‌های کمکی فاز ۰ (خارج از scope تسک‌های شماره‌گذاری‌شده)

### `scripts/create_evolution_db.sh`
- **چه می‌کند:** از ریشهٔ repo اجرا می‌شود؛ به `multi-messaging-platform` می‌رود و با `docker compose exec postgres` دیتابیس `evolution_db` را روی همان Postgres موجود پروژه می‌سازد (idempotent با `WHERE NOT EXISTS`) و `GRANT ALL` به `mmp_user` می‌دهد.
- **چرا لازم بود:** سرویس `evolution_api` در `docker-compose.yml` به `evolution_db` وصل است؛ mount خودکار `postgres-init` حذف شده و Evolution schema را خود image می‌سازد — فقط **خود دیتابیس** باید یک‌بار قبل از اولین `docker compose up evolution_api` وجود داشته باشد.
- **scope:** مانند `setup_vps.sh` — **خارج از scope تسک‌های شماره‌گذاری‌شده**؛ افزودهٔ جانبی در پرامپت پیاده‌سازی فاز ۰ (bootstrap DB). نگه داشته می‌شود چون بدون آن Evolution روی Postgres مشترک بالا نمی‌آید.

## TODO فاز ۳ — Webhook FastAPI
- مسیر webhook Evolution باید **عیناً** در روتر FastAPI ثبت شود:
  `POST /webhooks/whatsapp/evolution`
  (مقدار `EVOLUTION_WEBHOOK_URL` و `WEBHOOK_GLOBAL_URL` در docker-compose همین مسیر را دارد.)
