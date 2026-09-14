import Head from "next/head";
import Link from "next/link";
import { useEffect, useState } from "react";
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
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ApiError } from "@/lib/api";
import {
  archiveCampaign,
  fetchCampaigns,
  restoreCampaign,
} from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type { CampaignListItem } from "@/types/campaign";
import { campaignStatusLabel } from "@/utils/campaign-status";
import { canCreateCampaign, canControlCampaign, canViewCampaigns } from "@/utils/permissions";
import { accountDisplayName, orderedSenders } from "@/utils/sender-accounts";
import { toJalaliDateTime } from "@/utils/jalali";

function isCampaignRunningOrQueued(campaign: CampaignListItem): boolean {
  return campaign.status === "running" || campaign.status === "queued";
}

export default function CampaignsListPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canCreate = canCreateCampaign(role);
  const canView = canViewCampaigns(role);
  const canControl = canControlCampaign(role);

  const [items, setItems] = useState<CampaignListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [archiveView, setArchiveView] = useState<"active" | "archived">("active");
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [archiveConfirmCampaign, setArchiveConfirmCampaign] = useState<CampaignListItem | null>(
    null,
  );
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [restoreLoadingId, setRestoreLoadingId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const limit = 20;

  useEffect(() => {
    if (!canView) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- permission determines initial loading state
      setLoading(false);
      return;
    }

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchCampaigns({
          limit,
          offset,
          status: archiveView === "active" ? statusFilter || undefined : undefined,
          archived: archiveView === "archived",
        });
        if (!cancelled) {
          setItems(data.items);
          setTotal(data.total_count);
        }
      } catch {
        if (!cancelled) setError(t("campaignsLoadError"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [canView, offset, statusFilter, archiveView, t]);

  async function handleArchiveConfirmed() {
    if (!canControl || !archiveConfirmCampaign || archiveLoading) return;
    setArchiveLoading(true);
    setError(null);
    try {
      const result = await archiveCampaign(archiveConfirmCampaign.id);
      setItems((prev) => prev.filter((item) => item.id !== archiveConfirmCampaign.id));
      setTotal((prev) => Math.max(0, prev - 1));
      setArchiveConfirmCampaign(null);
      setNotice(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setArchiveLoading(false);
    }
  }

  async function handleRestore(campaignId: number) {
    if (!canControl || restoreLoadingId != null) return;
    setRestoreLoadingId(campaignId);
    setError(null);
    try {
      const result = await restoreCampaign(campaignId);
      setItems((prev) => prev.filter((item) => item.id !== campaignId));
      setTotal((prev) => Math.max(0, prev - 1));
      setNotice(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRestoreLoadingId(null);
    }
  }

  const archiveConfirmRunning = archiveConfirmCampaign
    ? isCampaignRunningOrQueued(archiveConfirmCampaign)
    : false;

  return (
    <>
      <Head>
        <title>{t("campaigns")}</title>
      </Head>
      <Layout title={t("campaigns")}>
        <PageContent>
          {!canView ? (
            <Panel>
              <EmptyState>{t("notAllowed")}</EmptyState>
            </Panel>
          ) : (
            <>
              <div className="mmp-stack">
                {archiveView === "active" && canCreate ? (
                  <Link href="/campaigns/create" className="mmp-btn mmp-btn--primary">
                    {t("createCampaign")}
                  </Link>
                ) : archiveView === "active" ? (
                  <span className="mmp-btn" style={{ opacity: 0.6 }}>
                    {t("createCampaign")}
                  </span>
                ) : null}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <Button
                    type="button"
                    size="sm"
                    variant={archiveView === "active" ? "primary" : "default"}
                    onClick={() => {
                      setOffset(0);
                      setArchiveView("active");
                    }}
                  >
                    فعال
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={archiveView === "archived" ? "primary" : "default"}
                    onClick={() => {
                      setOffset(0);
                      setArchiveView("archived");
                    }}
                  >
                    آرشیو
                  </Button>
                </div>
                {archiveView === "active" ? (
                  <FormField label={t("status")}>
                    <select
                      className={selectClassName}
                      style={{ width: "auto", minWidth: 160 }}
                      value={statusFilter}
                      onChange={(e) => {
                        setOffset(0);
                        setStatusFilter(e.target.value);
                      }}
                    >
                      <option value="">{t("allStatuses")}</option>
                      <option value="draft">{t("status_draft")}</option>
                      <option value="prepared">{t("status_prepared")}</option>
                      <option value="running">{t("status_running")}</option>
                      <option value="paused">{t("status_paused")}</option>
                    </select>
                  </FormField>
                ) : null}
              </div>

              {error ? <Alert>{error}</Alert> : null}
              {notice ? <Alert variant="success">{notice}</Alert> : null}

              <Panel title={`${t("campaigns")} (${total})`} flushTable>
                {loading ? (
                  <EmptyState>{t("loading")}</EmptyState>
                ) : items.length === 0 ? (
                  <EmptyState>
                    {archiveView === "archived" ? "کمپین آرشیوشده‌ای وجود ندارد." : t("noCampaigns")}
                  </EmptyState>
                ) : (
                  <TableWrap>
                    <table className={tableClassName}>
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>{t("campaignTitle")}</th>
                          <th>{t("platform")}</th>
                          <th>{t("status")}</th>
                          {archiveView === "archived" ? <th>تاریخ آرشیو</th> : null}
                          <th>{t("recipients")}</th>
                          {archiveView === "active" ? <th>{t("sender")}</th> : null}
                          <th>{t("actions")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map((c) => (
                          <tr key={c.id}>
                            <td>{c.id}</td>
                            <td>{c.title}</td>
                            <td>{c.platform}</td>
                            <td>{campaignStatusLabel(c.status, t)}</td>
                            {archiveView === "archived" ? (
                              <td>{c.archived_at ? toJalaliDateTime(c.archived_at) : "—"}</td>
                            ) : null}
                            <td>{c.total_recipients}</td>
                            {archiveView === "active" ? (
                              <td>
                                {(() => {
                                  const senders = orderedSenders(c);
                                  if (senders.length === 0) return t("senderModeAuto");
                                  const names = senders.slice(0, 2).map(accountDisplayName);
                                  const rest = senders.length - names.length;
                                  return `${names.join("، ")}${rest > 0 ? ` + ${t("senderMoreCount", { count: rest })}` : ""}`;
                                })()}
                              </td>
                            ) : null}
                            <td>
                              <div className="mmp-stack" style={{ gap: 6 }}>
                                {canControl && archiveView === "active" ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="primary"
                                    disabled={archiveLoading}
                                    onClick={() => setArchiveConfirmCampaign(c)}
                                  >
                                    {isCampaignRunningOrQueued(c) ? "توقف و آرشیو" : "آرشیو"}
                                  </Button>
                                ) : null}
                                <Link href={`/campaigns/${c.id}`}>{t("monitor")}</Link>
                                {canControl && archiveView === "archived" ? (
                                  <Button
                                    type="button"
                                    size="sm"
                                    variant="primary"
                                    disabled={restoreLoadingId === c.id}
                                    onClick={() => void handleRestore(c.id)}
                                  >
                                    {restoreLoadingId === c.id ? t("loading") : "بازیابی"}
                                  </Button>
                                ) : null}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </Panel>

              <ConfirmDialog
                open={archiveConfirmCampaign != null}
                title="آرشیو کردن کمپین"
                message={
                  archiveConfirmRunning
                    ? "با آرشیو کردن، ارسال‌های انجام‌نشده این کمپین متوقف می‌شوند. پیام‌هایی که قبلاً ارسال شده‌اند در تاریخچه باقی می‌مانند."
                    : "این کمپین از لیست فعال خارج می‌شود و تا زمان بازیابی قابل اجرا نخواهد بود."
                }
                confirmLabel={archiveConfirmRunning ? "توقف و آرشیو" : "آرشیو"}
                cancelLabel={t("cancel")}
                confirmLoading={archiveLoading}
                cancelDisabled={archiveLoading}
                onCancel={() => {
                  if (archiveLoading) return;
                  setArchiveConfirmCampaign(null);
                }}
                onConfirm={() => void handleArchiveConfirmed()}
              />

              <div className="mmp-stack" style={{ marginTop: 12 }}>
                <Button
                  type="button"
                  size="sm"
                  disabled={offset <= 0}
                  onClick={() => setOffset((o) => Math.max(0, o - limit))}
                >
                  {t("prevPage")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={offset + limit >= total}
                  onClick={() => setOffset((o) => o + limit)}
                >
                  {t("nextPage")}
                </Button>
              </div>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
