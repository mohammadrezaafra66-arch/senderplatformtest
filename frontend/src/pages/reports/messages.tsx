import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import {
  Alert,
  Button,
  EmptyState,
  FormField,
  PageContent,
  Panel,
  TableWrap,
  inputClassName,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchCampaignRecipients, fetchCampaigns, downloadCampaignRecipientsExport } from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type { CampaignListItem, CampaignRecipientItem } from "@/types/campaign";
import { SEND_STATUS_OPTIONS } from "@/utils/campaign-status";
import { toJalaliDateTime } from "@/utils/jalali";
import { canViewCampaigns, canViewMessageLogs } from "@/utils/permissions";

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

function sendStatusColor(status: string): string {
  if (status === "delivered") return "#166534";
  if (status === "failed_permanent" || status === "failed_retryable") return "#991b1b";
  if (status === "queued" || status === "processing") return "#1d4ed8";
  return "inherit";
}

export default function MessageLogsPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { role } = useAuth();
  const canView = canViewMessageLogs(role);
  const canListCampaigns = canViewCampaigns(role);

  const [campaignOptions, setCampaignOptions] = useState<CampaignListItem[]>([]);
  const [campaignIdInput, setCampaignIdInput] = useState("");
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | null>(null);
  const [sendStatusFilter, setSendStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<CampaignRecipientItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const limit = 30;

  useEffect(() => {
    if (!router.isReady || !canListCampaigns) return;
    const raw = router.query.campaign_id;
    if (typeof raw === "string" && raw.trim()) {
      setCampaignIdInput(raw.trim());
      const id = Number.parseInt(raw.trim(), 10);
      if (Number.isFinite(id)) setSelectedCampaignId(id);
    }
  }, [router.isReady, router.query.campaign_id, canListCampaigns]);

  useEffect(() => {
    if (!canListCampaigns) return;
    let cancelled = false;

    async function loadCampaigns() {
      try {
        const data = await fetchCampaigns({ limit: 100, offset: 0 });
        if (!cancelled) setCampaignOptions(data.items);
      } catch {
        // optional helper for admin/operator
      }
    }

    void loadCampaigns();
    return () => {
      cancelled = true;
    };
  }, [canListCampaigns]);

  const loadRecipients = useCallback(async () => {
    if (!canView || selectedCampaignId == null) return;

    setLoading(true);
    setError(null);
    try {
      const data = await fetchCampaignRecipients(selectedCampaignId, {
        limit,
        offset,
        send_status: sendStatusFilter || undefined,
      });
      setItems(data.items);
      setTotal(data.total_count);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("messageLogsLoadError"));
      setItems([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [canView, limit, offset, selectedCampaignId, sendStatusFilter, t]);

  useEffect(() => {
    if (selectedCampaignId != null) {
      void loadRecipients();
    }
  }, [loadRecipients, selectedCampaignId]);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    const id = Number.parseInt(campaignIdInput.trim(), 10);
    if (!Number.isFinite(id) || id < 1) {
      setError(t("invalidCampaignId"));
      return;
    }
    setOffset(0);
    setSelectedCampaignId(id);
    setError(null);
  }

  async function handleExportCsv() {
    if (selectedCampaignId == null) return;
    setExporting(true);
    setError(null);
    try {
      await downloadCampaignRecipientsExport(
        selectedCampaignId,
        sendStatusFilter || undefined,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("exportFailed"));
    } finally {
      setExporting(false);
    }
  }

  if (!canView) {
    return (
      <>
        <Head>
          <title>{t("messageLogs")}</title>
        </Head>
        <Layout title={t("messageLogs")}>
          <div style={{ textAlign: "right" }}>{t("notAllowed")}</div>
        </Layout>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>{t("messageLogs")}</title>
      </Head>
      <Layout title={t("messageLogs")}>
        <PageContent>
          <form
            onSubmit={handleSearch}
            style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "end" }}
          >
            {canListCampaigns && campaignOptions.length > 0 ? (
              <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                <span>{t("selectCampaign")}</span>
                <select
                  value={campaignIdInput}
                  onChange={(e) => {
                    setCampaignIdInput(e.target.value);
                    const id = Number.parseInt(e.target.value, 10);
                    if (Number.isFinite(id)) {
                      setOffset(0);
                      setSelectedCampaignId(id);
                    }
                  }}
                  style={{ ...inputStyle, minWidth: 220 }}
                >
                  <option value="">{t("chooseCampaign")}</option>
                  {campaignOptions.map((c) => (
                    <option key={c.id} value={String(c.id)}>
                      #{c.id} — {c.title}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
              <span>{t("campaignId")}</span>
              <input
                value={campaignIdInput}
                onChange={(e) => setCampaignIdInput(e.target.value)}
                placeholder="1"
                style={{ ...inputStyle, width: 120 }}
              />
            </label>

            <button
              type="submit"
              style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid rgba(0,0,0,0.2)" }}
            >
              {t("search")}
            </button>
          </form>

          {selectedCampaignId != null ? (
            <div style={{ marginTop: 10, fontSize: 14, display: "flex", gap: 12, flexWrap: "wrap" }}>
              <span>
                {t("campaignId")}: <strong>{selectedCampaignId}</strong>
              </span>
              {canListCampaigns ? (
                <Link href={`/campaigns/${selectedCampaignId}`}>{t("monitor")}</Link>
              ) : null}
            </div>
          ) : null}

              {error ? <Alert>{error}</Alert> : null}

          {selectedCampaignId != null ? (
            <div style={panelStyle}>
              <div
                style={{
                  padding: "10px 12px",
                  background: "rgba(0,0,0,0.02)",
                  borderBottom: "1px solid rgba(0,0,0,0.08)",
                  fontWeight: 700,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <span>
                  {t("messageLogs")} ({total})
                </span>
                <div className="mmp-stack">
                  <Button
                    type="button"
                    size="sm"
                    disabled={exporting}
                    onClick={() => void handleExportCsv()}
                  >
                    {exporting ? t("loading") : t("exportCsv")}
                  </Button>
                  <select
                  value={sendStatusFilter}
                  onChange={(e) => {
                    setOffset(0);
                    setSendStatusFilter(e.target.value);
                  }}
                  style={inputStyle}
                >
                  <option value="">{t("allStatuses")}</option>
                  {SEND_STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
                </div>
              </div>

              {loading ? (
                <div style={{ padding: 12 }}>{t("loading")}</div>
              ) : items.length === 0 ? (
                <div style={{ padding: 12, opacity: 0.75 }}>{t("noMessageLogs")}</div>
              ) : (
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                      <th style={{ padding: 8, textAlign: "right" }}>#</th>
                      <th style={{ padding: 8, textAlign: "right" }}>{t("phone")}</th>
                      <th style={{ padding: 8, textAlign: "right" }}>{t("name")}</th>
                      <th style={{ padding: 8, textAlign: "right" }}>render</th>
                      <th style={{ padding: 8, textAlign: "right" }}>send</th>
                      <th style={{ padding: 8, textAlign: "right" }}>{t("updatedAt")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((r) => (
                      <tr key={r.id} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                        <td style={{ padding: 8 }}>{r.id}</td>
                        <td style={{ padding: 8 }}>{r.phone ?? "—"}</td>
                        <td style={{ padding: 8 }}>
                          {[r.first_name, r.last_name].filter(Boolean).join(" ") || "—"}
                        </td>
                        <td style={{ padding: 8 }}>{r.render_status}</td>
                        <td style={{ padding: 8, color: sendStatusColor(r.send_status) }}>
                          {r.send_status}
                        </td>
                        <td style={{ padding: 8 }}>{toJalaliDateTime(r.updated_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              <div style={{ display: "flex", gap: 10, padding: 12 }}>
                <button
                  type="button"
                  disabled={offset <= 0}
                  onClick={() => setOffset((o) => Math.max(0, o - limit))}
                  style={{ padding: "8px 12px", borderRadius: 8 }}
                >
                  {t("prevPage")}
                </button>
                <button
                  type="button"
                  disabled={offset + limit >= total}
                  onClick={() => setOffset((o) => o + limit)}
                  style={{ padding: "8px 12px", borderRadius: 8 }}
                >
                  {t("nextPage")}
                </button>
              </div>
            </div>
          ) : (
            <div className="mmp-muted" style={{ marginTop: 16 }}>
              {t("messageLogsHint")}
            </div>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
