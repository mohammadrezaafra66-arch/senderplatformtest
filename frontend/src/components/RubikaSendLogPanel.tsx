import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, tableClassName, TableWrap } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchRubikaSendLog } from "@/lib/rubika-api";
import type { RubikaSendLogItem } from "@/types/rubika";
import { toJalaliDateTime } from "@/utils/jalali";

const PAGE_SIZE = 25;

function statusColor(status: string | null): string {
  if (!status) return "#6b7280";
  if (status.includes("success") || status === "delivered") return "#166534";
  if (status.includes("retryable")) return "#b45309";
  if (status.includes("permanent")) return "#991b1b";
  return "#6b7280";
}

export function RubikaSendLogPanel() {
  const { t } = useTranslation();
  const [items, setItems] = useState<RubikaSendLogItem[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (newOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fetchRubikaSendLog({ limit: PAGE_SIZE, offset: newOffset });
        setItems(result.items);
        setTotalCount(result.total_count);
        setOffset(newOffset);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const hasNext = offset + PAGE_SIZE < totalCount;
  const hasPrev = offset > 0;

  return (
    <div style={{ display: "grid", gap: 12 }}>
      {error ? <Alert variant="error">{error}</Alert> : null}

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong>
          {t("rubikaSendLogTitle")} ({totalCount})
        </strong>
        <Button type="button" size="sm" onClick={() => void load(offset)} disabled={loading}>
          {loading ? t("loading") : t("refresh")}
        </Button>
      </div>

      {loading && items.length === 0 ? (
        <EmptyState>{t("loading")}</EmptyState>
      ) : items.length === 0 ? (
        <EmptyState>{t("rubikaSendLogEmpty")}</EmptyState>
      ) : (
        <TableWrap>
          <table className={tableClassName}>
            <thead>
              <tr>
                <th>{t("rubikaSendLogColTime")}</th>
                <th>{t("rubikaSendLogColCampaign")}</th>
                <th>{t("rubikaSendLogColAccount")}</th>
                <th>{t("rubikaSendLogColContact")}</th>
                <th>{t("rubikaSendLogColStatus")}</th>
                <th>{t("rubikaSendLogColError")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.message_id}>
                  <td style={{ fontSize: 12 }}>{toJalaliDateTime(item.created_at)}</td>
                  <td>{item.campaign_title ?? `#${item.campaign_id}`}</td>
                  <td>{item.account_label ?? `#${item.account_id}`}</td>
                  <td style={{ direction: "ltr", textAlign: "left", fontSize: 12 }}>
                    {item.contact_phone ?? "—"}
                  </td>
                  <td>
                    <span style={{ color: statusColor(item.status), fontWeight: 600, fontSize: 12 }}>
                      {item.status ?? "—"}
                    </span>
                  </td>
                  <td style={{ fontSize: 12, color: item.error_message ? "#991b1b" : undefined }}>
                    {item.error_message ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableWrap>
      )}

      <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
        <Button type="button" size="sm" disabled={!hasPrev || loading} onClick={() => void load(Math.max(0, offset - PAGE_SIZE))}>
          {t("rubikaSendLogPrev")}
        </Button>
        <Button type="button" size="sm" disabled={!hasNext || loading} onClick={() => void load(offset + PAGE_SIZE)}>
          {t("rubikaSendLogNext")}
        </Button>
      </div>
    </div>
  );
}
