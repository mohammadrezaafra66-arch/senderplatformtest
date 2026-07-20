import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, TableWrap, tableClassName } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchAccountPool } from "@/lib/telegram-api";
import type { TelegramPoolAccount } from "@/types/telegram";

const badgeStyle = (color: string): CSSProperties => ({
  display: "inline-block",
  padding: "3px 8px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: color,
  color: "#fff",
});

const ltrCell: CSSProperties = { direction: "ltr", textAlign: "left" };

function healthColor(isHealthy: boolean): string {
  return isHealthy ? "#166534" : "#991b1b";
}

function warmedColor(isWarmedUp: boolean): string {
  return isWarmedUp ? "#166534" : "#b45309";
}

export function TelegramAccountPool() {
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<TelegramPoolAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchAccountPool();
      setAccounts(result);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : t("telegramPoolLoadError", { defaultValue: "خطا در دریافت استخر اکانت‌ها" }),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

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
            padding: "12px 12px 0",
            marginBottom: 8,
          }}
        >
          <Button type="button" size="sm" onClick={() => void load()} disabled={loading}>
            {loading
              ? t("loading", { defaultValue: "در حال بارگذاری…" })
              : t("refresh", { defaultValue: "بارگذاری مجدد" })}
          </Button>
        </div>

        {loading && accounts.length === 0 ? (
          <EmptyState>{t("loading", { defaultValue: "در حال بارگذاری…" })}</EmptyState>
        ) : accounts.length === 0 ? (
          <EmptyState>
            {t("telegramPoolEmpty", {
              defaultValue: "هنوز هیچ اکانت تلگرامی در استخر ثبت نشده است.",
            })}
          </EmptyState>
        ) : (
          <TableWrap>
            <table className={tableClassName}>
              <thead>
                <tr>
                  <th>{t("telegramPoolColAccount", { defaultValue: "شناسه اکانت" })}</th>
                  <th>{t("telegramPoolColHealth", { defaultValue: "وضعیت" })}</th>
                  <th>{t("telegramPoolColWarmed", { defaultValue: "گرم‌شده" })}</th>
                  <th>{t("telegramPoolColSent", { defaultValue: "ارسال امروز" })}</th>
                  <th>{t("telegramPoolColError", { defaultValue: "آخرین خطا" })}</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((acc) => (
                  <tr key={acc.account_id}>
                    <td style={ltrCell}>#{acc.account_id}</td>
                    <td>
                      <span style={badgeStyle(healthColor(acc.is_healthy))}>
                        {acc.is_healthy
                          ? t("telegramHealthy", { defaultValue: "سالم" })
                          : t("telegramUnhealthy", { defaultValue: "دارای اشکال" })}
                      </span>
                    </td>
                    <td>
                      <span style={badgeStyle(warmedColor(acc.is_warmed_up))}>
                        {acc.is_warmed_up
                          ? t("telegramWarmed", { defaultValue: "گرم‌شده" })
                          : t("telegramWarming", { defaultValue: "در حال گرم‌شدن" })}
                      </span>
                    </td>
                    <td style={ltrCell}>
                      {acc.sent_today} / {acc.daily_cap_today}
                    </td>
                    <td
                      style={{
                        fontSize: 12,
                        color: acc.last_error_message ? "#991b1b" : undefined,
                      }}
                    >
                      {acc.last_error_message ?? "—"}
                    </td>
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
