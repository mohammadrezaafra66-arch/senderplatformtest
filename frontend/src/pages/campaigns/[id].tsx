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
  PageContent,
  Panel,
  PanelContent,
  TableWrap,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  fetchCampaignDetail,
  fetchCampaignRecipients,
  startCampaign,
  stopCampaign,
} from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type { CampaignDetail, CampaignRecipientItem } from "@/types/campaign";
import { campaignStatusLabel, SEND_STATUS_OPTIONS } from "@/utils/campaign-status";
import { canControlCampaign, canViewCampaigns } from "@/utils/permissions";

export default function CampaignMonitorPage() {
  const { t } = useTranslation();
  const router = useRouter();
  const { role } = useAuth();
  const canControl = canControlCampaign(role);
  const canView = canViewCampaigns(role);

  const rawId = router.query.id;
  const campaignId = typeof rawId === "string" ? Number.parseInt(rawId, 10) : NaN;

  const [campaign, setCampaign] = useState<CampaignDetail | null>(null);
  const [recipients, setRecipients] = useState<CampaignRecipientItem[]>([]);
  const [recipientsTotal, setRecipientsTotal] = useState(0);
  const [sendStatusFilter, setSendStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadCampaign = useCallback(async () => {
    if (!canView || !Number.isFinite(campaignId)) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, recip] = await Promise.all([
        fetchCampaignDetail(campaignId),
        fetchCampaignRecipients(campaignId, {
          limit: 50,
          send_status: sendStatusFilter || undefined,
        }),
      ]);
      setCampaign(detail);
      setRecipients(recip.items);
      setRecipientsTotal(recip.total_count);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("campaignsLoadError"));
    } finally {
      setLoading(false);
    }
  }, [campaignId, canView, sendStatusFilter, t]);

  useEffect(() => {
    if (!router.isReady) return;
    void loadCampaign();
  }, [router.isReady, loadCampaign]);

  async function handleStart() {
    if (!canControl || !Number.isFinite(campaignId)) return;
    setActionLoading(true);
    setNotice(null);
    try {
      const result = await startCampaign(campaignId);
      setNotice(result.message);
      await loadCampaign();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setActionLoading(false);
    }
  }

  async function handleStop() {
    if (!canControl || !Number.isFinite(campaignId)) return;
    setActionLoading(true);
    setNotice(null);
    try {
      const result = await stopCampaign(campaignId);
      setNotice(result.message);
      await loadCampaign();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setActionLoading(false);
    }
  }

  if (!canView) {
    return (
      <>
        <Head>
          <title>{t("campaigns")}</title>
        </Head>
        <Layout title={t("campaigns")}>
          <PageContent>{t("notAllowed")}</PageContent>
        </Layout>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>
          {t("campaigns")} #{Number.isFinite(campaignId) ? campaignId : "—"}
        </title>
      </Head>
      <Layout
        title={
          campaign
            ? `${campaign.title} (${campaignStatusLabel(campaign.status, t)})`
            : `${t("campaigns")} #${String(rawId ?? "")}`
        }
      >
        <PageContent>
          <div className="mmp-stack">
            <Link href="/campaigns" className="mmp-link-muted">
              ← {t("backToCampaigns")}
            </Link>
            {Number.isFinite(campaignId) ? (
              <Link
                href={`/reports/messages?campaign_id=${campaignId}`}
                className="mmp-link-muted"
              >
                {t("viewFullMessageLogs")}
              </Link>
            ) : null}
          </div>

          {error ? <Alert>{error}</Alert> : null}
          {notice ? <Alert variant="success">{notice}</Alert> : null}

          {loading || !campaign ? (
            <div className="mmp-muted" style={{ marginTop: 16 }}>
              {t("loading")}
            </div>
          ) : (
            <>
              <div className="mmp-stack" style={{ marginTop: 16 }}>
                <Button
                  type="button"
                  variant="primary"
                  disabled={!canControl || actionLoading}
                  onClick={() => void handleStart()}
                >
                  {t("start")}
                </Button>
                <Button
                  type="button"
                  disabled={!canControl || actionLoading}
                  onClick={() => void handleStop()}
                >
                  {t("stop")}
                </Button>
              </div>

              <Panel title={t("campaignProgress")}>
                <PanelContent>
                  <div>
                    {t("progress")}: {campaign.stats.progress_percent.toFixed(1)}%
                  </div>
                  <div className="mmp-progress" style={{ marginTop: 8 }}>
                    <div
                      className="mmp-progress__bar"
                      style={{ width: `${Math.min(100, campaign.stats.progress_percent)}%` }}
                    />
                  </div>
                  <div className="mmp-stack" style={{ marginTop: 10 }}>
                    <span>
                      {t("kpiMessagesSent")}: {campaign.stats.sent}
                    </span>
                    <span>
                      {t("kpiMessagesFailed")}: {campaign.stats.failed}
                    </span>
                    <span>
                      {t("queuePending")}: {campaign.stats.queued}
                    </span>
                    <span>
                      {t("recipients")}: {campaign.stats.total_recipients}
                    </span>
                  </div>
                </PanelContent>
              </Panel>

              <Panel
                title={`${t("messageLogs")} (${recipientsTotal})`}
                headerExtra={
                  <select
                    className={selectClassName}
                    style={{ width: "auto", minWidth: 160 }}
                    value={sendStatusFilter}
                    onChange={(e) => setSendStatusFilter(e.target.value)}
                  >
                    <option value="">{t("allStatuses")}</option>
                    {SEND_STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                }
                flushTable
              >
                {recipients.length === 0 ? (
                  <EmptyState>{t("noRecipients")}</EmptyState>
                ) : (
                  <TableWrap>
                    <table className={tableClassName}>
                      <thead>
                        <tr>
                          <th>{t("phone")}</th>
                          <th>{t("name")}</th>
                          <th>render</th>
                          <th>send</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recipients.map((r) => (
                          <tr key={r.id}>
                            <td>{r.phone ?? "—"}</td>
                            <td>
                              {[r.first_name, r.last_name].filter(Boolean).join(" ") || "—"}
                            </td>
                            <td>{r.render_status}</td>
                            <td>{r.send_status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </Panel>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
