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

## TODO فاز ۳ — Webhook FastAPI
- مسیر webhook Evolution باید **عیناً** در روتر FastAPI ثبت شود:
  `POST /webhooks/whatsapp/evolution`
  (مقدار `EVOLUTION_WEBHOOK_URL` و `WEBHOOK_GLOBAL_URL` در docker-compose همین مسیر را دارد.)
