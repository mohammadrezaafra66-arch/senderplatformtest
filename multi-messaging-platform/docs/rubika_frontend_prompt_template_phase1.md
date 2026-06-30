# قالب پرامپت Cursor — پنل فرانت‌اند روبیکا (فاز ۱ از ۳)

> این فایل یک **قالب** است، نه دستور اجراشده. وقتی فاز ۵-۷ بک‌اند (پایش گروه،
> هوش مصنوعی، استاتوس) آماده شد و واقعاً وقت ساخت فرانت رسید، این سند را
> بازبینی و به‌روز کن (مخصوصاً بخش «خارج از scope») قبل از دادن به Cursor —
> چون آن بخش فرض می‌کند آن فازها هنوز ساخته نشده‌اند.

## اصول قابل‌استفاده مجدد (برای هر پرامپت فرانت آینده، نه فقط روبیکا)

- **ایزولاسیون اجباری**: هیچ فایل پلتفرم دیگر (WhatsApp/Evolution/Telegram/Bale) ویرایش نشود — نه import نه منطق. فقط مسیرهای جدید مخصوص پلتفرم خودش.
- **سبک پروژه**: این پروژه Tailwind ندارد — inline styles، هم‌سبک با `DelaySetting.tsx` و `WhatsAppEvolutionPanel.tsx`.
- **الگوی API**: از `apiFetch` موجود استفاده شود (همان الگوی `whatsapp-api.ts`)، نه `fetch` خام.
- **investigation قبل از کد**: قبل از نوشتن UI، endpoint های واقعی بک‌اند با grep پیدا شوند. اگر endpoint ای موجود نبود، آن بخش UI با باکس "TODO: backend endpoint لازم است" (دکمه disabled) ساخته شود — حدس زده نشود، مسیر جعلی ساخته نشود.
- **گزارش پایان**: کدام endpoint واقعاً پیدا/وصل شد، کدام TODO ماند — صریح اعلام شود.

## متن کامل پرامپت اصلی (نسخه خرداد ۱۴۰۵، برای فاز ۱ از ۳ — مدیریت اکانت + ورود OTP)

```
هدف: ساخت پنل مدیریت اکانت و ورود روبیکا (فاز ۱ از ۳ فاز فرانت‌اند روبیکا) — کاملاً ماژولار و ایزوله از پلتفرم‌های دیگر.

══════════════════════════════
قانون ایزولاسیون (must follow)
══════════════════════════════
- هیچ فایل مربوط به WhatsApp/Evolution/Telegram/Bale را ویرایش نکن (نه import، نه تغییر منطق).
- تمام فایل‌های جدید فقط در مسیرهای زیر ساخته شوند:
  frontend/src/pages/rubika/
  frontend/src/components/rubika/
  frontend/src/lib/rubika-api.ts
  frontend/src/types/rubika.ts
- تنها استثنا: افزودن یک خط لینک ناوبری به فایل nav/sidebar/layout موجود (فقط افزودن، بدون تغییر منطق موجود).
- این پروژه Tailwind ندارد — حتماً از inline styles استفاده کن (هم‌سبک با DelaySetting.tsx و WhatsAppEvolutionPanel.tsx). استفاده از کلاس‌های Tailwind باعث بی‌استایل شدن کامل می‌شود.
- برای فراخوانی API از تابع apiFetch موجود پروژه استفاده کن (همان که در DelaySetting.tsx و WhatsAppEvolutionPanel.tsx استفاده شده)، نه fetch خام.

══════════════════════════════
مرحله ۰ — investigation (الزامی قبل از نوشتن هر کد)
══════════════════════════════
این دستورات را اجرا کن و بر اساس خروجی واقعی کد بنویس، نه بر اساس فرض:

۱. پیدا کردن فایل(های) API روبیکا در بک‌اند:
   find . -iname "*rubika*" -path "*/core_engine/*"

۲. لیست کامل route های موجود در آن فایل(ها):
   grep -n "@router\.\(get\|post\|put\|patch\|delete\)" <فایل‌های پیدا شده>

۳. بررسی مدل اکانت روبیکا (فیلدهای واقعی):
   grep -n "class.*Rubika\|class.*Account" core_engine/models.py

۴. بررسی الگوی apiFetch موجود:
   cat frontend/src/lib/whatsapp-api.ts | head -40

۵. پیدا کردن فایل ناوبری/layout:
   find frontend/src -iname "*nav*" -o -iname "*sidebar*" -o -iname "*layout*"

اگر هر کدام از endpoint های زیر در خروجی مرحله ۲ پیدا نشد، آن بخش UI را با یک باکس "TODO: backend endpoint لازم است" بساز (دکمه را disabled کن) و در گزارش نهایی صریحاً اعلام کن کدام endpoint موجود نیست — حدس نزن و مسیر جعلی نساز.

endpoint هایی که باید پیدا کنی:
- GET لیست اکانت‌های روبیکا (با وضعیت/فاز)
- POST ارسال کد OTP به شماره
- POST تأیید کد OTP (و در صورت نیاز pass_key)
- PUT/PATCH تغییر فاز اکانت (day/night/listener/status)
- (اختیاری) POST اضافه کردن اکانت جدید به pool

══════════════════════════════
مرحله ۱ — types
══════════════════════════════
frontend/src/types/rubika.ts را بساز با interface هایی که دقیقاً منطبق بر پاسخ واقعی API است (بر اساس مرحله ۰).
حداقل شامل:
- RubikaAccount { id, phone, status: "active"|"resting"|"banned", phase: "day"|"night"|"listener"|"status", session_connected: boolean, ... }
- RubikaOtpRequest, RubikaOtpVerifyRequest

══════════════════════════════
مرحله ۲ — frontend/src/lib/rubika-api.ts
══════════════════════════════
تمام توابع API روبیکا اینجا، با همان الگوی apiFetch که در whatsapp-api.ts دیدی:
- getRubikaAccounts()
- sendRubikaOtp(phone)
- verifyRubikaOtp(phone, code, pass_key?)
- updateRubikaPhase(accountId, phase)
- (در صورت وجود endpoint) addRubikaAccount(...)

══════════════════════════════
مرحله ۳ — کامپوننت‌ها
══════════════════════════════

frontend/src/components/rubika/RubikaPhaseBadge.tsx
— badge کوچک رنگی برای نمایش فاز (day=آبی، night=بنفش، listener=خاکستری، status=سبز)، با dropdown برای تغییر فاز که updateRubikaPhase را صدا می‌زند.

frontend/src/components/rubika/RubikaAccountCard.tsx
— کارت یک اکانت: شماره، badge وضعیت (active/resting/banned با رنگ سبز/زرد/قرمز)، badge وضعیت session (متصل/قطع)، RubikaPhaseBadge.

frontend/src/components/rubika/RubikaAccountsList.tsx
— لیست کارت‌ها، useEffect برای getRubikaAccounts، loading/error state، دکمه رفرش.

frontend/src/components/rubika/RubikaOtpLoginForm.tsx
— فرم دو مرحله‌ای:
  مرحله ۱: input شماره + دکمه "ارسال کد"
  مرحله ۲ (بعد از موفقیت مرحله ۱): input کد ۵/۶ رقمی + دکمه "تأیید"
  اگر API پاسخ نیاز به pass_key داد: نمایش input سوم برای pass_key
  بعد از موفقیت: فراخوانی رفرش لیست اکانت‌ها + نمایش پیام موفقیت فارسی
  مدیریت خطا: پیام‌های فارسی واضح (کد اشتباه، شماره نامعتبر و غیره) بر اساس پاسخ واقعی بک‌اند

══════════════════════════════
مرحله ۴ — صفحه
══════════════════════════════
frontend/src/pages/rubika/index.tsx
— عنوان "مدیریت اکانت‌های روبیکا"، RubikaOtpLoginForm بالا، RubikaAccountsList پایین. RTL، فارسی، هم‌سبک بصری با بقیه صفحات پروژه.

══════════════════════════════
مرحله ۵ — لینک ناوبری
══════════════════════════════
در فایل nav/sidebar موجود (از مرحله ۰)، فقط یک آیتم جدید اضافه کن: "روبیکا" → /rubika
هیچ آیتم دیگری را تغییر نده.

══════════════════════════════
خارج از scope این پرامپت (نساز)
══════════════════════════════
- ویرایش زمان‌بندی روز/شب (نیاز به backend endpoint جدید دارد — پرامپت جداگانه)
- UI کمپین مخصوص روبیکا (از مسیر عمومی /campaigns استفاده می‌شود — پرامپت جداگانه)
- پایش گروه‌ها، هوش مصنوعی، استاتوس/روبینو (بک‌اند هنوز ساخته نشده)

══════════════════════════════
بعد از اتمام
══════════════════════════════
خروجی tsc --noEmit و lint را تأیید کن.
گزارش بده: کدام endpoint ها واقعاً پیدا و وصل شدند، کدام TODO ماندند.
سپس:
git add frontend/src/pages/rubika frontend/src/components/rubika frontend/src/lib/rubika-api.ts frontend/src/types/rubika.ts <فایل nav ویرایش‌شده>
git commit -m "feat: Rubika account management and OTP login panel (modular, isolated)"
git push
```

## نکته هنگام استفاده واقعی (بعداً)

موارد زیر را قبل از اجرای واقعی این پرامپت با کد فعلی ریپو مطابقت بده — احتمالاً عوض شده‌اند:

- endpoint های واقعی موجود: `POST /accounts/{id}/rubika/session/register`، `POST /accounts/{id}/rubika/session/verify` (هر دو در `core_engine/api/accounts.py`، نه یک فایل جدای `rubika.py` — فاز ۴ سند هنوز `core_engine/api/rubika.py` را نساخته).
- endpoint لیست اکانت‌ها با فاز/pool **هنوز ساخته نشده** (`GET /accounts` عمومی فعلاً phase/pool را برنمی‌گرداند) — قبل از اجرای این پرامپت باید اول این endpoint در بک‌اند اضافه شود.
- endpoint تغییر فاز (`PUT/PATCH`) **هنوز ساخته نشده**.
- اگر فازهای ۵-۷ تا آن موقع ساخته شده باشند، بخش «خارج از scope» را به‌روز کن.
