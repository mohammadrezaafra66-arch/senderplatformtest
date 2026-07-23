import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import {
  Alert,
  Button,
  EmptyState,
  FormField,
  selectClassName,
  TableWrap,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchLeads } from "@/lib/telegram-api";
import type { TelegramLead } from "@/types/telegram";
import { toJalaliDateTime } from "@/utils/jalali";

const LIMIT_OPTIONS = [50, 100, 200];

const ltrCell: CSSProperties = { direction: "ltr", textAlign: "left" };

export function TelegramLeadsPanel() {
  const { t } = useTranslation();
  const [leads, setLeads] = useState<TelegramLead[]>([]);
  const [limit, setLimit] = useState(100);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // شمارنده ترتیب درخواست‌ها: پاسخ‌های قدیمی که دیرتر می‌رسند نادیده گرفته می‌شوند.
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const result = await fetchLeads(limit);
      if (seq !== requestSeq.current) return;
      setLeads(result);
    } catch (err) {
      if (seq !== requestSeq.current) return;
      setError(
        err instanceof ApiError
          ? err.message
          : t("telegramLeadsLoadError", { defaultValue: "خطا در دریافت لیدها" }),
      );
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, [limit, t]);

  // تغییر limit هویت load را عوض می‌کند و دقیقاً یک بار واکشی مجدد رخ می‌دهد.
  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* پنل والد flushTable است و padding ندارد؛ جدول لبه‌به‌لبه می‌ماند
          و فقط هدر و هشدارها padding می‌گیرند. */}
      {error ? (
        <div style={{ padding: "12px 12px 0" }}>
          <Alert variant="error">{error}</Alert>
        </div>
      ) : null}

      <div>
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 12,
            flexWrap: "wrap",
            padding: "12px 12px 0",
            marginBottom: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <FormField label={t("telegramLeadsLimit", { defaultValue: "تعداد نمایش" })}>
              <select
                className={selectClassName}
                value={limit}
                disabled={loading}
                onChange={(event) => setLimit(Number(event.target.value))}
              >
                {LIMIT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </FormField>
            <Button type="button" size="sm" onClick={() => void load()} disabled={loading}>
              {loading
                ? t("loading", { defaultValue: "در حال بارگذاری…" })
                : t("refresh", { defaultValue: "بارگذاری مجدد" })}
            </Button>
          </div>
        </div>

        {loading && leads.length === 0 ? (
          <EmptyState>{t("loading", { defaultValue: "در حال بارگذاری…" })}</EmptyState>
        ) : leads.length === 0 ? (
          <EmptyState>
            {t("telegramLeadsEmpty", {
              defaultValue: "هنوز لیدی از تلگرام استخراج نشده است.",
            })}
          </EmptyState>
        ) : (
          <TableWrap>
            <table className={tableClassName}>
              <thead>
                <tr>
                  <th>{t("telegramLeadsColPhone", { defaultValue: "شماره موبایل" })}</th>
                  <th>{t("telegramLeadsColUser", { defaultValue: "نام کاربری" })}</th>
                  <th>{t("telegramLeadsColSource", { defaultValue: "منبع" })}</th>
                  <th>{t("telegramLeadsColSeen", { defaultValue: "اولین مشاهده" })}</th>
                </tr>
              </thead>
              <tbody>
                {/*
                  phone_number کلید طبیعی این جدول است، اما هیچ قید یکتایی در
                  دیتابیس آن را تضمین نمی‌کند؛ برای جلوگیری از کلید تکراری در
                  React، ایندکس هم به کلید اضافه شده است.
                */}
                {leads.map((lead, index) => (
                  <tr key={`${lead.phone_number}-${index}`}>
                    <td style={ltrCell}>{lead.phone_number}</td>
                    <td style={ltrCell}>{lead.username ? `@${lead.username}` : "—"}</td>
                    <td>{lead.source ?? "—"}</td>
                    <td>{lead.first_seen_at ? toJalaliDateTime(lead.first_seen_at) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </div>
    </div>
  );
}
