import Head from "next/head";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import {
  Alert,
  EmptyState,
  PageContent,
  Panel,
  PanelContent,
  StatCard,
  TableWrap,
  tableClassName,
} from "@/components/ui";
import {
  fetchDashboardSummary,
  fetchQueuesStatus,
  fetchRecentCampaigns,
  fetchWorkersStatus,
} from "@/lib/dashboard-api";
import { useAuth } from "@/state/auth";
import type { CampaignListItem } from "@/types/campaign";
import type { DashboardSummary, QueueStatusItem, WorkerStatusItem } from "@/types/dashboard";
import { campaignStatusLabel } from "@/utils/campaign-status";
import { toJalaliDateTime } from "@/utils/jalali";
import { canCreateCampaign } from "@/utils/permissions";

export default function Home() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [queues, setQueues] = useState<QueueStatusItem[]>([]);
  const [workers, setWorkers] = useState<WorkerStatusItem[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [summaryData, queuesData, workersData] = await Promise.all([
          fetchDashboardSummary(),
          fetchQueuesStatus(),
          fetchWorkersStatus(),
        ]);
        if (cancelled) return;
        setSummary(summaryData);
        setQueues(queuesData);
        setWorkers(workersData);

        if (canCreateCampaign(role)) {
          try {
            const recent = await fetchRecentCampaigns(5);
            if (!cancelled) setCampaigns(recent);
          } catch {
            if (!cancelled) setCampaigns([]);
          }
        }
      } catch {
        if (!cancelled) setError(t("dashboardLoadError"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [role, t]);

  return (
    <>
      <Head>
        <title>{t("dashboard")}</title>
        <meta name="description" content="Sender Platform Dashboard" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.ico" />
      </Head>
      <Layout title={t("dashboard")}>
        <PageContent>
          <h1 style={{ fontSize: 24, marginBottom: 8 }}>{t("welcome")}</h1>
          <p className="mmp-muted" style={{ marginBottom: 16 }}>
            {t("today")}: {toJalaliDateTime()}
          </p>

          {error ? <Alert>{error}</Alert> : null}

          {loading || !summary ? (
            <div className="mmp-muted" style={{ marginBottom: 18 }}>
              {t("loading")}
            </div>
          ) : (
            <>
              <section className="mmp-kpi-grid" style={{ marginBottom: 18 }}>
                <StatCard label={t("kpiCampaignsTotal")} value={summary.campaigns_total} />
                <StatCard label={t("kpiCampaignsRunning")} value={summary.campaigns_running} />
                <StatCard label={t("kpiCampaignsPaused")} value={summary.campaigns_paused} />
                <StatCard label={t("kpiMessagesTotal")} value={summary.messages_total} />
                <StatCard label={t("kpiMessagesSent")} value={summary.messages_sent} />
                <StatCard label={t("kpiMessagesFailed")} value={summary.messages_failed} />
                <StatCard label={t("kpiAccountsActive")} value={summary.accounts_active} />
                <StatCard label={t("kpiAccountsBanned")} value={summary.accounts_banned} />
              </section>

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                  gap: 14,
                }}
              >
                <Panel title={t("queueStatus")} flushTable>
                  <TableWrap>
                    <table className={tableClassName}>
                      <thead>
                        <tr>
                          <th>{t("queueName")}</th>
                          <th>{t("queuePending")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {queues.map((q) => (
                          <tr key={q.name}>
                            <td>{q.name}</td>
                            <td>{q.pending}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                </Panel>

                <Panel title={t("workerStatus")} flushTable>
                  <TableWrap>
                    <table className={tableClassName}>
                      <thead>
                        <tr>
                          <th>{t("workerName")}</th>
                          <th>{t("workerState")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {workers.map((w) => (
                          <tr key={w.name}>
                            <td>{w.name}</td>
                            <td>{w.status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                </Panel>
              </div>
            </>
          )}

          <div className="mmp-stack" style={{ marginTop: 18 }}>
            {canCreateCampaign(role) ? (
              <Link href="/campaigns/create" className="mmp-btn mmp-btn--primary">
                {t("createCampaign")}
              </Link>
            ) : null}
            <Link href="/campaigns" className="mmp-btn">
              {t("campaigns")}
            </Link>
            <Link href="/contacts" className="mmp-btn">
              {t("contacts")}
            </Link>
            <Link href="/reports/messages" className="mmp-btn">
              {t("messageLogs")}
            </Link>
          </div>

          {canCreateCampaign(role) ? (
            <Panel title={t("recentCampaigns")} flushTable>
              {loading ? (
                <EmptyState>{t("loading")}</EmptyState>
              ) : campaigns.length === 0 ? (
                <EmptyState>{t("noCampaigns")}</EmptyState>
              ) : (
                <TableWrap>
                  <table className={tableClassName}>
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>{t("campaignTitle")}</th>
                        <th>{t("platform")}</th>
                        <th>{t("status")}</th>
                        <th>{t("recipients")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {campaigns.map((c) => (
                        <tr key={c.id}>
                          <td>
                            <Link href={`/campaigns/${c.id}`}>{c.id}</Link>
                          </td>
                          <td>{c.title}</td>
                          <td>{c.platform}</td>
                          <td>{campaignStatusLabel(c.status, t)}</td>
                          <td>{c.total_recipients}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableWrap>
              )}
            </Panel>
          ) : null}
        </PageContent>
      </Layout>
    </>
  );
}
