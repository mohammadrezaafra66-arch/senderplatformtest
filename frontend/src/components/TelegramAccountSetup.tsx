import { useTranslation } from "react-i18next";

import { Alert } from "@/components/ui";

/**
 * ورود با شماره تلفن (MTProto) عمداً پیاده‌سازی نشده است.
 *
 * وضعیت تأییدشده سمت بک‌اند:
 *
 * 1. `start_phone_login` — در
 *    `multi-messaging-platform/core_engine/api/telegram_mtproto.py:10`
 *    ایمپورت می‌شود، اما در
 *    `multi-messaging-platform/core_engine/services/telegram_session_setup.py`
 *    اصلاً تعریف نشده است. نتیجه: ImportError هنگام بارگذاری اپلیکیشن.
 *
 * 2. `verify_phone_code` — امضای واقعی آن در
 *    `multi-messaging-platform/core_engine/services/telegram_session_setup.py:8`
 *    به این صورت است:
 *      `async def verify_phone_code(db, *, account_id, phone_number, code)`
 *    یعنی پارامترها keyword-only هستند و `two_step_password` وجود ندارد،
 *    ولی روتر آن را با آرگومان‌های positional صدا می‌زند. نتیجه: TypeError.
 *
 * 3. بدنه `verify_phone_code` یک stub است و همیشه
 *    `{"status": "error", ...}` برمی‌گرداند.
 *
 * تا وقتی هر سه مورد بالا اصلاح نشده‌اند، هیچ درخواستی به
 * `/telegram-mtproto/session/start` یا `/telegram-mtproto/session/verify`
 * حتی به شبکه هم نمی‌رسد. به همین دلیل این کامپوننت فرم ورود ندارد و
 * هیچ فراخوانی شبکه‌ای انجام نمی‌دهد — نمایش فرمی که همیشه خطا می‌دهد
 * از اعلام صادقانه‌ی «فعال نیست» بدتر است.
 *
 * برای فعال‌سازی: `start_phone_login` را اضافه کنید، امضای
 * `verify_phone_code` را با فراخوانی روتر هم‌راستا کنید، و سپس
 * توابع متناظر را به `frontend/src/lib/telegram-api.ts` اضافه کنید.
 */
export function TelegramAccountSetup() {
  const { t } = useTranslation();

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <Alert variant="error">
        {t("telegramSetupUnavailable", {
          defaultValue: "ورود با شماره تلفن هنوز فعال نیست — سرویس سمت سرور تکمیل نشده است.",
        })}
      </Alert>
      <div style={{ fontSize: 12, opacity: 0.7 }}>
        {t("telegramSetupUnavailableDetail", {
          defaultValue: "پس از تکمیل سرویس نشست تلگرام، فرم ورود در همین بخش فعال می‌شود.",
        })}
      </div>
    </div>
  );
}
