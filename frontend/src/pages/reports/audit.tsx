import Head from "next/head";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import {
  Alert,
  Button,
  EmptyState,
  PageContent,
  Panel,
  TableWrap,
  inputClassName,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchAuditLogs } from "@/lib/audit-api";
import { useAuth } from "@/state/auth";
import type { AuditLogItem } from "@/types/audit";
import { toJalaliDateTime } from "@/utils/jalali";
import { canViewAudit } from "@/utils/permissions";

const panelStyle: React.CSSProperties = {
  marginTop: 16,
  border: "1px solid rgba(0,0,0,0.12)",
  borderRadius: 12,
  overflow: "hidden",
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(0,0,0,0.2)",
};

function formatDetails(details: Record<string, unknown> | null): string {
  if (!details || Object.keys(details).length === 0) return "—";
  try {
    const text = JSON.stringify(details);
    return text.length > 120 ? `${text.slice(0, 120)}…` : text;
  } catch {
    return "—";
  }
}

export default function AuditLogsPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canView = canViewAudit(role);

  const [limit, setLimit] = useState(50);
  const [items, setItems] = useState<AuditLogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState("");
  const [usernameFilter, setUsernameFilter] = useState("");

  const loadLogs = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAuditLogs(limit);
      setItems(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("auditLogsLoadError"));
    } finally {
      setLoading(false);
    }
  }, [canView, limit, t]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  const filtered = items.filter((row) => {
    if (actionFilter && !row.action.toLowerCase().includes(actionFilter.toLowerCase())) {
      return false;
    }
    if (usernameFilter && !(row.username ?? "").toLowerCase().includes(usernameFilter.toLowerCase())) {
      return false;
    }
    return true;
  });

  return (
    <>
      <Head>
        <title>{t("auditLogs")}</title>
      </Head>
      <Layout title={t("auditLogs")}>
        <PageContent>
          {!canView ? (
            <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.14)" }}>
              {t("notAllowed")}
            </div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end" }}>
                <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                  <span>{t("auditActionFilter")}</span>
                  <input
                    value={actionFilter}
                    onChange={(e) => setActionFilter(e.target.value)}
                    placeholder="create_account"
                    style={{ ...inputStyle, minWidth: 160 }}
                  />
                </label>
                <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                  <span>{t("username")}</span>
                  <input
                    value={usernameFilter}
                    onChange={(e) => setUsernameFilter(e.target.value)}
                    style={{ ...inputStyle, minWidth: 140 }}
                  />
                </label>
                <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                  <span>{t("limit")}</span>
                  <select
                    value={limit}
                    onChange={(e) => setLimit(Number.parseInt(e.target.value, 10))}
                    style={inputStyle}
                  >
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={200}>200</option>
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => void loadLogs()}
                  style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid rgba(0,0,0,0.2)" }}
                >
                  {t("refresh")}
                </button>
              </div>

              {error ? (
                <div role="alert" style={{ marginTop: 12, color: "#991b1b", fontSize: 14 }}>
                  {error}
                </div>
              ) : null}

              <div style={panelStyle}>
                <div
                  style={{
                    padding: "10px 12px",
                    background: "rgba(0,0,0,0.02)",
                    borderBottom: "1px solid rgba(0,0,0,0.08)",
                    fontWeight: 700,
                  }}
                >
                  {t("auditLogs")} ({filtered.length})
                </div>

                {loading ? (
                  <div style={{ padding: 12 }}>{t("loading")}</div>
                ) : filtered.length === 0 ? (
                  <div style={{ padding: 12, opacity: 0.75 }}>{t("noAuditLogs")}</div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                    <thead>
                      <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                        <th style={{ padding: 8, textAlign: "right" }}>#</th>
                        <th style={{ padding: 8, textAlign: "right" }}>{t("timestamp")}</th>
                        <th style={{ padding: 8, textAlign: "right" }}>{t("username")}</th>
                        <th style={{ padding: 8, textAlign: "right" }}>{t("action")}</th>
                        <th style={{ padding: 8, textAlign: "right" }}>{t("resource")}</th>
                        <th style={{ padding: 8, textAlign: "right" }}>{t("details")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((row) => (
                        <tr key={row.id} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                          <td style={{ padding: 8 }}>{row.id}</td>
                          <td style={{ padding: 8, whiteSpace: "nowrap" }}>
                            {toJalaliDateTime(row.timestamp)}
                          </td>
                          <td style={{ padding: 8 }}>{row.username ?? "—"}</td>
                          <td style={{ padding: 8 }}>{row.action}</td>
                          <td style={{ padding: 8 }}>
                            {row.resource_type}:{row.resource_id}
                          </td>
                          <td
                            style={{ padding: 8, fontSize: 12, opacity: 0.85, maxWidth: 280 }}
                            title={row.details ? JSON.stringify(row.details) : undefined}
                          >
                            {formatDetails(row.details)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
