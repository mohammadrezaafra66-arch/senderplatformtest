import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, FormField, inputClassName } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchSchedule, updateSchedule } from "@/lib/telegram-api";
import type { TelegramSchedule } from "@/types/telegram";

type Draft = {
  start: string;
  end: string;
};

function toDraft(schedule: TelegramSchedule): Draft {
  return { start: String(schedule.start_hour), end: String(schedule.end_hour) };
}

function parseHour(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  if (!Number.isInteger(value)) return null;
  if (value < 0 || value > 23) return null;
  return value;
}

export function TelegramScheduleForm() {
  const { t } = useTranslation();
  // مقدار ذخیره‌شده روی سرور — مبنای تشخیص تغییر پیش‌نویس
  const [saved, setSaved] = useState<TelegramSchedule | null>(null);
  const [draft, setDraft] = useState<Draft>({ start: "", end: "" });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchSchedule();
      setSaved(result);
      setDraft(toDraft(result));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : t("telegramScheduleLoadError", { defaultValue: "خطا در دریافت بازه زمانی" }),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const isDirty =
    saved != null && (draft.start !== String(saved.start_hour) || draft.end !== String(saved.end_hour));

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    // اعتبارسنجی کامل سمت کلاینت — بک‌اند هیچ اعتبارسنجی‌ای انجام نمی‌دهد.
    const startHour = parseHour(draft.start);
    const endHour = parseHour(draft.end);
    if (startHour === null || endHour === null) {
      setError(
        t("telegramScheduleRangeError", {
          defaultValue: "ساعت شروع و پایان باید عددی بین ۰ تا ۲۳ باشد.",
        }),
      );
      return;
    }
    // بازه شبانه (مثلاً ۲۲ تا ۸) معتبر است؛ فقط تساوی رد می‌شود.
    if (startHour === endHour) {
      setError(
        t("telegramScheduleSameError", {
          defaultValue: "ساعت شروع و پایان نمی‌توانند یکسان باشند.",
        }),
      );
      return;
    }

    setSaving(true);
    try {
      await updateSchedule({ start_hour: startHour, end_hour: endHour });
      // مقدار نمایش‌داده‌شده از سرور خوانده می‌شود، نه از پیش‌نویس محلی.
      await load();
      setNotice(t("telegramScheduleSaved", { defaultValue: "بازه زمانی ذخیره شد." }));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : t("telegramScheduleSaveError", { defaultValue: "خطا در ذخیره بازه زمانی" }),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {error ? <Alert variant="error">{error}</Alert> : null}
      {notice ? <Alert variant="success">{notice}</Alert> : null}

      <div>
        {loading && saved === null ? (
          <EmptyState>{t("loading", { defaultValue: "در حال بارگذاری…" })}</EmptyState>
        ) : (
          <form onSubmit={(event) => void handleSubmit(event)} style={{ display: "grid", gap: 12 }}>
            <div
              style={{
                display: "grid",
                gap: 12,
                gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
              }}
            >
              <FormField label={t("telegramScheduleStart", { defaultValue: "ساعت شروع" })}>
                <input
                  className={inputClassName}
                  type="number"
                  min={0}
                  max={23}
                  step={1}
                  style={{ direction: "ltr", textAlign: "left" }}
                  value={draft.start}
                  disabled={saving}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, start: event.target.value }))
                  }
                />
              </FormField>
              <FormField label={t("telegramScheduleEnd", { defaultValue: "ساعت پایان" })}>
                <input
                  className={inputClassName}
                  type="number"
                  min={0}
                  max={23}
                  step={1}
                  style={{ direction: "ltr", textAlign: "left" }}
                  value={draft.end}
                  disabled={saving}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, end: event.target.value }))
                  }
                />
              </FormField>
            </div>

            <div>
              <Button type="submit" variant="primary" size="sm" disabled={saving || !isDirty}>
                {saving
                  ? t("saving", { defaultValue: "در حال ذخیره…" })
                  : t("save", { defaultValue: "ذخیره" })}
              </Button>
            </div>

            {/* BE-6: پاسخ GET /schedule بین «ذخیره‌شده» و «پیش‌فرض» تفاوتی قائل نیست. */}
            <div style={{ fontSize: 12, opacity: 0.7 }}>
              {t("telegramScheduleHint", {
                defaultValue:
                  "اگر بازه‌ای ذخیره نشده باشد، مقدار پیش‌فرض ۹ تا ۲۱ نمایش داده می‌شود. برای ثبت قطعی، یک بار ذخیره کنید.",
              })}
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
