import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { MessageSenderCell } from "@/components/MessageSenderCell";
import { MessageTextCell } from "@/components/MessageTextCell";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { MessageDetailModal } from "@/components/MessageDetailModal";
import { CampaignSenderSelector, type SenderMode } from "@/components/CampaignSenderSelector";
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
import { fetchAccounts } from "@/lib/accounts-api";
import { campaignAccountError } from "@/lib/campaign-account-errors";
import { campaignPreparationError } from "@/lib/campaign-preparation-errors";
import { campaignStartError } from "@/lib/campaign-start-errors";
import { getFailureReasonFa } from "@/lib/failure-reason";
import { deliveryNote, readNote, sendStatusLabel } from "@/utils/message-status";
import {
  archiveCampaign,
  fetchCampaignDetail,
  fetchCampaignPreflight,
  fetchCampaignRecipientDetail,
  fetchCampaignRecipients,
  prepareCampaign,
  restoreCampaign,
  startCampaign,
  stopCampaign,
  campaignSenderSaveNoticeKey,
  updateCampaignAccounts,
} from "@/lib/campaign-api";
import { useAuth } from "@/state/auth";
import type {
  CampaignDetail,
  CampaignPreflight,
  CampaignRecipientItem,
  MessageLogDetail,
} from "@/types/campaign";
import type { AccountItem } from "@/types/account";
import { campaignStatusLabel, SEND_STATUS_OPTIONS } from "@/utils/campaign-status";
import { canControlCampaign, canViewCampaigns } from "@/utils/permissions";
import { accountDisplayName, orderedSenders, resolveCampaignSenderStatusLabel } from "@/utils/sender-accounts";
import { toJalaliDateTime } from "@/utils/jalali";
import { formatAccountCapRemaining } from "@/utils/account-message-limits";
import {
  controlledProductionStatusLabel,
  formatPreflightBlockers,
  formatReadinessRatio,
  formatUnknownOrCount,
  hasTechnicalStartBlockers,
  isCapacityUnknown,
  isStartActionable,
  nextPreflightRefreshMs,
  preflightNeedsAutoRefresh,
  preparationStatusLabel,
  requiresControlledProductionConfirmation,
  resolvePreparationUiState,
  resolvePreflightAccountHealthLabel,
  resolvePreflightExecutionLabel,
  resolvePreflightOperationalAlerts,
} from "@/utils/campaign-preflight-display";

function isCampaignRunningOrQueued(status: string, stats?: { queued?: number }): boolean {
  return status === "running" || status === "queued" || (stats?.queued ?? 0) > 0;
}

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
  const [prepareLoading, setPrepareLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<AccountItem[]>([]);
  const [accountsLoading, setAccountsLoading] = useState(true);
  const [accountsError, setAccountsError] = useState<string | null>(null);
  const [senderMode, setSenderMode] = useState<SenderMode>("auto");
  const [selectedAccountIds, setSelectedAccountIds] = useState<number[]>([]);
  const [senderSaving, setSenderSaving] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detail, setDetail] = useState<MessageLogDetail | null>(null);
  const [preflight, setPreflight] = useState<CampaignPreflight | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [startConfirmOpen, setStartConfirmOpen] = useState(false);
  const [startConfirmLoading, setStartConfirmLoading] = useState(false);
  const [archiveConfirmOpen, setArchiveConfirmOpen] = useState(false);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [restoreLoading, setRestoreLoading] = useState(false);

  const loadAccounts = useCallback(async () => {
    setAccountsLoading(true);
    setAccountsError(null);
    try {
      const result = await fetchAccounts();
      setAccounts(result.items);
    } catch (err) {
      setAccountsError(err instanceof ApiError ? err.message : t("accountsLoadError"));
    } finally {
      setAccountsLoading(false);
    }
  }, [t]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data load
    if (canControl) void loadAccounts();
  }, [canControl, loadAccounts]);

  const loadCampaign = useCallback(async () => {
    if (!canView || !Number.isFinite(campaignId)) return;
    setLoading(true);
    setError(null);
    try {
      const [detail, recip, pf] = await Promise.all([
        fetchCampaignDetail(campaignId),
        fetchCampaignRecipients(campaignId, {
          limit: 50,
          send_status: sendStatusFilter || undefined,
        }),
        fetchCampaignPreflight(campaignId).catch((err: unknown) => {
          setPreflightError(err instanceof ApiError ? err.message : t("campaignsLoadError"));
          return null;
        }),
      ]);
      if (canControl) {
        void loadAccounts();
      }
      setCampaign(detail);
      const ids = detail.account_ids ?? [];
      setSenderMode(ids.length > 0 ? "manual" : "auto");
      setSelectedAccountIds(ids);
      setRecipients(recip.items);
      setRecipientsTotal(recip.total_count);
      setPreflight(pf);
      if (pf) setPreflightError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("campaignsLoadError"));
    } finally {
      setLoading(false);
    }
  }, [campaignId, canView, canControl, sendStatusFilter, t, loadAccounts]);

  useEffect(() => {
    if (!router.isReady) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial and filter-driven remote data load
    void loadCampaign();
  }, [router.isReady, loadCampaign]);

  useEffect(() => {
    if (!preflightNeedsAutoRefresh(preflight)) return;
    const delay = nextPreflightRefreshMs(preflight);
    const timer = window.setTimeout(() => {
      if (!Number.isFinite(campaignId)) return;
      void fetchCampaignPreflight(campaignId)
        .then(setPreflight)
        .catch(() => undefined);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [preflight, campaignId]);

  const startActionable = isStartActionable(preflight);
  const controlledConfirmationRequired = requiresControlledProductionConfirmation(preflight);
  const controlledStatusLabel = controlledProductionStatusLabel(preflight, t);
  const technicalStartBlocked = hasTechnicalStartBlockers(preflight);
  const showTechnicalStartBlockers = Boolean(
    preflight && technicalStartBlocked && !controlledConfirmationRequired,
  );
  const leftoverBlockers = preflight ? formatPreflightBlockers(preflight, t) : [];
  const operationalAlerts = resolvePreflightOperationalAlerts(preflight);
  const capacityUnknown = isCapacityUnknown(preflight);
  const unknownLabel = t("nA");
  const capacityUnknownLabel = t("capacityUnknownLive");
  const preparationState = resolvePreparationUiState(preflight, prepareLoading);
  const preparationLabel = preparationStatusLabel(preparationState, t);
  const showPrepareRetry =
    preparationState === "needs_retry" ||
    (prepareLoading && preparationState !== "ready");
  const prepareBlocked = Boolean(
    preflight?.preparation_blockers && preflight.preparation_blockers.length > 0,
  );
  const enrichedAccounts = useMemo(() => {
    if (!preflight?.accounts?.length) return accounts;
    const byId = new Map(preflight.accounts.map((row) => [row.account_id, row]));
    return accounts.map((account) => {
      const row = byId.get(account.id);
      if (!row) return account;
      return {
        ...account,
        execution_blocker_label: row.execution_blocker_label ?? undefined,
        execution_blocker_code: row.execution_blocker_code ?? undefined,
        execution_ready: row.execution_ready,
      };
    });
  }, [accounts, preflight]);
  const safetyLabel = (() => {
    const state = preflight?.execution_safety_state;
    if (state === "BLOCKED" || state === "PAUSED_SAFETY") return t("campaignSafetyBlocked");
    if (state === "CAPACITY_WARNING" || state === "WAITING_WINDOW" || state === "WAITING_CAPACITY") {
      return t("campaignSafetyWarning");
    }
    return t("campaignSafetyReady");
  })();

  const isArchived = Boolean(campaign?.archived_at);
  const archiveConfirmRunning = campaign
    ? isCampaignRunningOrQueued(campaign.status, campaign.stats)
    : false;

  async function handleStartConfirmed(confirmControlledProduction: boolean) {
    if (!canControl || !Number.isFinite(campaignId)) return;
    if (startConfirmLoading || actionLoading) return;
    setStartConfirmLoading(true);
    setActionLoading(true);
    setNotice(null);
    setError(null);
    try {
      const result = await startCampaign(campaignId, {
        confirmControlledProduction,
      });
      setStartConfirmOpen(false);
      const scheduled =
        typeof result.queue_jobs_created === "number"
          ? result.queue_jobs_created
          : typeof result.messages_scheduled === "number"
            ? result.messages_scheduled
            : null;
      setNotice(
        scheduled != null && scheduled > 0
          ? `${result.message} (${scheduled})`
          : result.message,
      );
      await loadCampaign();
    } catch (err) {
      setError(campaignStartError(err, t));
    } finally {
      setStartConfirmLoading(false);
      setActionLoading(false);
    }
  }

  function handleStartClick() {
    if (!canControl || !Number.isFinite(campaignId) || !startActionable) return;
    if (controlledConfirmationRequired) {
      setStartConfirmOpen(true);
      return;
    }
    void handleStartConfirmed(false);
  }

  async function handlePrepare() {
    if (!canControl || !Number.isFinite(campaignId)) return;
    setPrepareLoading(true);
    setNotice(null);
    setError(null);
    try {
      const result = await prepareCampaign(campaignId);
      setNotice(result.message || t("campaignPrepareSuccess"));
      await loadCampaign();
    } catch (err) {
      setError(campaignPreparationError(err, t));
    } finally {
      setPrepareLoading(false);
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

  async function handleArchiveConfirmed() {
    if (!canControl || !Number.isFinite(campaignId) || archiveLoading) return;
    setArchiveLoading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await archiveCampaign(campaignId);
      setArchiveConfirmOpen(false);
      setNotice(result.message);
      await loadCampaign();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setArchiveLoading(false);
    }
  }

  async function handleRestore() {
    if (!canControl || !Number.isFinite(campaignId) || restoreLoading) return;
    setRestoreLoading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await restoreCampaign(campaignId);
      setNotice(result.message);
      await loadCampaign();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRestoreLoading(false);
    }
  }

  async function handleSaveSenders() {
    if (!canControl || !Number.isFinite(campaignId)) return;
    if (senderMode === "manual" && selectedAccountIds.length === 0) {
      setError(t("senderManualRequired"));
      return;
    }
    setSenderSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await updateCampaignAccounts(
        campaignId,
        senderMode === "auto" ? [] : selectedAccountIds,
      );
      setNotice(t(campaignSenderSaveNoticeKey(result)));
      await loadCampaign();
    } catch (err) {
      setError(campaignAccountError(err, t));
    } finally {
      setSenderSaving(false);
    }
  }

  async function openMessageDetail(item: CampaignRecipientItem) {
    if (!Number.isFinite(campaignId)) return;
    setDetailOpen(true);
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    try {
      const data = await fetchCampaignRecipientDetail(campaignId, item.id);
      setDetail(data);
    } catch (err) {
      setDetailError(err instanceof ApiError ? err.message : t("messageLogsLoadError"));
    } finally {
      setDetailLoading(false);
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
              {isArchived ? (
                <Alert>
                  این کمپین آرشیو شده است.
                  {campaign.archived_at ? ` (${toJalaliDateTime(campaign.archived_at)})` : ""}
                </Alert>
              ) : null}

              <Alert variant={preparationState === "ready" ? "success" : undefined}>
                <strong>{t("campaignPreparationTitle")}: </strong>
                {preparationLabel}
                {typeof preflight?.prepared_messages === "number" && preflight.campaign_prepared
                  ? ` (${preflight.prepared_messages})`
                  : ""}
              </Alert>

              {controlledStatusLabel ? (
                <Alert variant="success">
                  {controlledStatusLabel}
                </Alert>
              ) : null}

              <div className="mmp-stack" style={{ marginTop: 16 }}>
                {!isArchived && showPrepareRetry ? (
                  <Button
                    type="button"
                    disabled={!canControl || prepareLoading || actionLoading || prepareBlocked}
                    onClick={() => void handlePrepare()}
                  >
                    {prepareLoading ? t("campaignPrepareInProgress") : t("campaignPrepareAction")}
                  </Button>
                ) : null}
                {!isArchived ? (
                  <>
                    <Button
                      type="button"
                      variant="primary"
                      disabled={
                        !canControl ||
                        actionLoading ||
                        prepareLoading ||
                        startConfirmLoading ||
                        !startActionable ||
                        preparationState !== "ready"
                      }
                      onClick={() => handleStartClick()}
                    >
                      {t("start")}
                    </Button>
                    <Button
                      type="button"
                      disabled={!canControl || actionLoading || prepareLoading}
                      onClick={() => void handleStop()}
                    >
                      {t("stop")}
                    </Button>
                  </>
                ) : null}
                {canControl && isArchived ? (
                  <Button
                    type="button"
                    variant="primary"
                    disabled={restoreLoading}
                    onClick={() => void handleRestore()}
                  >
                    {restoreLoading ? t("loading") : "بازیابی"}
                  </Button>
                ) : null}
                {canControl && !isArchived ? (
                  <Button
                    type="button"
                    disabled={actionLoading || prepareLoading || archiveLoading}
                    onClick={() => setArchiveConfirmOpen(true)}
                  >
                    {archiveConfirmRunning ? "توقف و آرشیو" : "آرشیو"}
                  </Button>
                ) : null}
              </div>

              {preparationState === "needs_retry" ? (
                <p className="mmp-muted">{t("campaignPreparationRetryHint")}</p>
              ) : null}
              {preflight?.preparation_blockers && preflight.preparation_blockers.length > 0 ? (
                <Alert>
                  <div className="mmp-stack">
                    <strong>{t("campaignPreparationBlockersTitle")}</strong>
                    {preflight.preparation_blockers.map((item) => (
                      <div key={item.code}>{item.message}</div>
                    ))}
                  </div>
                </Alert>
              ) : null}
              {preflightError ? <Alert>{preflightError}</Alert> : null}
              {operationalAlerts.redisUnavailable ? (
                <Alert>{t("campaignRedisUnavailable")}</Alert>
              ) : null}
              {operationalAlerts.workerNotRunning ? (
                <Alert>
                  {t("campaignWorkerNotRunning", {
                    count: preflight?.assigned_accounts ?? 0,
                  })}
                </Alert>
              ) : null}
              {operationalAlerts.noSenders ? (
                <Alert>{t("campaignNoSendersAssigned")}</Alert>
              ) : null}
              {showTechnicalStartBlockers && leftoverBlockers.length > 0 ? (
                <Alert>
                  <div className="mmp-stack">
                    {leftoverBlockers.map((line) => (
                      <div key={line}>{line}</div>
                    ))}
                  </div>
                </Alert>
              ) : null}
              {preflight && preflight.warnings.length > 0 ? (
                <Alert>
                  {preflight.warnings.map((item) => item.message).join(" ")}
                </Alert>
              ) : null}

              {preflight ? (
                <Panel title={t("campaignCapacityTitle")}>
                  <PanelContent>
                    <div className="mmp-stack">
                      <div>
                        {t("campaignReadinessTitle")}: {safetyLabel}
                        {preflight.execution_safety_state ? ` (${preflight.execution_safety_state})` : ""}
                      </div>
                      <div>
                        {t("campaignRemainingMessages")}:{" "}
                        {preflight.progress.pending ?? preflight.total_messages}
                      </div>
                      <div>
                        {t("campaignAssignedAccounts")}: {preflight.assigned_accounts}
                      </div>
                      <div>
                        {t("campaignAccountReadinessTitle")}:{" "}
                        {formatReadinessRatio(
                          preflight.campaign_eligible_accounts ??
                            preflight.ready_accounts ??
                            preflight.usable_accounts,
                          preflight.assigned_accounts,
                          unknownLabel,
                          { unknown: false },
                        )}
                      </div>
                      <div>
                        {t("campaignExecutionReadinessTitle")}:{" "}
                        {formatReadinessRatio(
                          preflight.execution_usable_accounts,
                          preflight.assigned_accounts,
                          unknownLabel,
                          { unknown: capacityUnknown },
                        )}
                      </div>
                      <div>
                        {t("campaignPreparationTitle")}:{" "}
                        {preflight.campaign_prepared
                          ? t("campaignPreparedYes")
                          : t("campaignPreparedNo")}
                        {typeof preflight.prepared_messages === "number"
                          ? ` (${preflight.prepared_messages})`
                          : ""}
                      </div>
                      <div className="mmp-muted">{t("campaignReadinessSemanticsNote")}</div>
                      <div>
                        {t("campaignSenderReadyCount")}:{" "}
                        {formatReadinessRatio(
                          preflight.campaign_eligible_accounts ??
                            preflight.ready_accounts ??
                            preflight.usable_accounts,
                          preflight.assigned_accounts,
                          unknownLabel,
                          { unknown: false },
                        )}
                        {preflight.assignment_materialized === false
                          ? ` (${t("campaignPreparedSeparate")})`
                          : ""}
                      </div>
                      <div>
                        {t("campaignUsableAccounts")}:{" "}
                        {formatReadinessRatio(
                          preflight.usable_accounts,
                          preflight.assigned_accounts,
                          unknownLabel,
                          { unknown: capacityUnknown },
                        )}
                        {!capacityUnknown && typeof preflight.execution_usable_accounts === "number"
                          ? ` · execution ${preflight.execution_usable_accounts}`
                          : ""}
                      </div>
                      <div>
                        {t("campaignBlockedAccounts")}: {preflight.blocked_accounts}
                      </div>
                      <div>
                        {t("campaignTodayCapacity")}:{" "}
                        {formatUnknownOrCount(
                          preflight.estimated_today_capacity,
                          capacityUnknown,
                          capacityUnknownLabel,
                        )}
                      </div>
                      <div>
                        {t("campaignHourlyCapacity")}:{" "}
                        {formatUnknownOrCount(
                          preflight.estimated_hourly_capacity,
                          capacityUnknown,
                          capacityUnknownLabel,
                        )}
                      </div>
                      <div>
                        {t("campaignImmediateCapacity")}:{" "}
                        {formatUnknownOrCount(
                          preflight.immediate_capacity,
                          capacityUnknown,
                          capacityUnknownLabel,
                        )}
                      </div>
                      <div>
                        {t("campaignCircuitState")}: {preflight.circuit_state ?? t("nA")}
                      </div>
                      <div>
                        {t("campaignCapacityForecast")}:{" "}
                        {preflight.estimated_completion_at
                          ? new Date(preflight.estimated_completion_at).toLocaleString("fa-IR")
                          : t("nA")}
                      </div>
                      <div className="mmp-muted">{t("campaignEstimateNote")}</div>
                    </div>
                    {preflight.accounts.length > 0 ? (
                      <TableWrap>
                        <table className={tableClassName}>
                          <thead>
                            <tr>
                              <th>{t("campaignCapacityTableSender")}</th>
                              <th>{t("campaignCapacityTableAssigned")}</th>
                              <th>{t("campaignCapacityTableHealth")}</th>
                              <th>{t("campaignCapacityTableAccountHealth")}</th>
                              <th>{t("campaignCapacityTableExecution")}</th>
                              <th>{t("campaignCapacityTableDaily")}</th>
                              <th>{t("campaignCapacityTableHourly")}</th>
                              <th>{t("campaignCapacityTableNext")}</th>
                              <th>{t("campaignCapacityTableStatus")}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {preflight.accounts.map((row) => (
                              <tr key={row.account_id}>
                                <td>{row.display_identity || row.label || `#${row.account_id}`}</td>
                                <td>{row.assigned}</td>
                                <td>{row.health ?? "—"}</td>
                                <td>{resolvePreflightAccountHealthLabel(row, t)}</td>
                                <td>{resolvePreflightExecutionLabel(row, t)}</td>
                                <td>
                                  {formatAccountCapRemaining({
                                    unlimited: row.daily_unlimited,
                                    remaining: row.daily_remaining,
                                    quotaKnown: row.quota_known,
                                    unlimitedLabel:
                                      t("dailyCapUnlimited") || "سقف روزانه: بدون محدودیت",
                                    unknownLabel:
                                      t("capacityUnknownLive") || "ظرفیت لحظه‌ای نامشخص",
                                  })}
                                </td>
                                <td>
                                  {formatAccountCapRemaining({
                                    unlimited: row.hourly_unlimited,
                                    remaining: row.hourly_remaining,
                                    quotaKnown: row.quota_known,
                                    unlimitedLabel:
                                      t("hourlyCapUnlimited") || "سقف ساعتی: بدون محدودیت",
                                    unknownLabel:
                                      t("capacityUnknownLive") || "ظرفیت لحظه‌ای نامشخص",
                                  })}
                                </td>
                                <td>
                                  {row.next_allowed_at
                                    ? new Date(row.next_allowed_at).toLocaleString("fa-IR")
                                    : "—"}
                                </td>
                                <td>
                                  {row.execution_ready || row.eligible_now
                                    ? t("campaignSafetyReady")
                                    : resolvePreflightExecutionLabel(row, t)}
                                  {row.bottleneck ? ` · ${t("campaignBottlenecks")}` : ""}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </TableWrap>
                    ) : null}
                  </PanelContent>
                </Panel>
              ) : null}

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

              <Panel title={t("senderAccountsTitle")}>
                <PanelContent>
                  <div className="mmp-muted">
                    {t("senderMode")}: {(campaign.account_ids?.length ?? 0) > 0 ? t("senderModeManual") : t("senderModeAuto")}
                  </div>
                  {(campaign.account_ids?.length ?? 0) > 0 ? (
                    <ol className="mmp-sender-summary">
                      {orderedSenders(campaign).map((sender) => (
                        <li key={sender.account_id}>
                          <strong>{accountDisplayName(sender)}</strong>
                          <span>{sender.platform}</span>
                          <span>
                            {resolveCampaignSenderStatusLabel(sender, t)}
                            {(() => {
                              const pfRow = preflight?.accounts.find(
                                (row) => row.account_id === sender.account_id,
                              );
                              if (pfRow?.execution_blocker_label) {
                                return ` · ${pfRow.execution_blocker_label}`;
                              }
                              if (sender.campaign_eligible === false) {
                                return ` · ${sender.blocker_label || t("senderAssignedNotReadyHint")}`;
                              }
                              if (pfRow?.execution_ready || pfRow?.eligible_now) {
                                return ` · ${t("campaignExecutionReadyNow")}`;
                              }
                              if (sender.campaign_eligible === true) {
                                return ` · ${t("campaignAccountHealthReady")}`;
                              }
                              return "";
                            })()}
                          </span>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {canControl ? (
                    <div style={{ marginTop: 16 }}>
                      <CampaignSenderSelector
                        platform={campaign.platform}
                        accounts={enrichedAccounts}
                        mode={senderMode}
                        selectedIds={selectedAccountIds}
                        loading={accountsLoading}
                        loadError={accountsError}
                        disabled={senderSaving || isArchived}
                        onModeChange={setSenderMode}
                        onSelectedIdsChange={setSelectedAccountIds}
                        onRetry={() => void loadAccounts()}
                      />
                      <p className="mmp-muted">{t("senderChangeWarning")}</p>
                      <Button
                        variant="primary"
                        disabled={
                          isArchived ||
                          senderSaving ||
                          (senderMode === "manual" && selectedAccountIds.length === 0)
                        }
                        onClick={() => void handleSaveSenders()}
                      >
                        {senderSaving ? t("loading") : t("saveSenderAccounts")}
                      </Button>
                    </div>
                  ) : null}
                </PanelContent>
              </Panel>

              <Panel title={t("campaignSettings")}>
                <PanelContent>
                  <div className="mmp-stack">
                    <span>
                      {t("useGpt")}: {campaign.use_gpt ? t("yes") : t("no")}
                    </span>
                    <span>
                      {t("includeProducts")}: {campaign.include_products ? t("yes") : t("no")}
                    </span>
                    <span>
                      {t("status")}: {campaignStatusLabel(campaign.status, t)}
                      {campaign.stop_label ? ` — ${campaign.stop_label}` : ""}
                    </span>
                    <span>
                      {t("renderBatch")}: {campaign.latest_render_batch_id ?? "—"}
                    </span>
                    <span>
                      {t("renderVersion")}: {campaign.render_version ?? "—"}
                    </span>
                  </div>
                </PanelContent>
              </Panel>

              <Panel title={t("committedFinalSamples")}>
                <PanelContent>
                  {(campaign.committed_renders ?? []).length === 0 ? (
                    <p className="mmp-muted">{t("noCommittedFinal")}</p>
                  ) : (
                    <div className="mmp-stack" style={{ gap: 12 }}>
                      <p className="mmp-muted">{t("committedFinalHint")}</p>
                      {(campaign.committed_renders ?? []).map((sample) => (
                        <div key={sample.rendered_message_id} className="campaign-committed-sample">
                          <strong>{t("committedFinalLabel")}</strong>
                          {sample.recipient_name ? (
                            <div className="mmp-muted">{sample.recipient_name}</div>
                          ) : null}
                          <pre className="message-detail-text">{sample.final_text}</pre>
                          {sample.variation_id ? (
                            <div className="mmp-muted">
                              {t("gptVariationId")}: {sample.variation_id}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
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
                        {sendStatusLabel(s)}
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
                          <th>{t("messageText")}</th>
                          <th>render</th>
                          <th>send</th>
                          <th>{t("sender")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {recipients.map((r) => (
                          <tr key={r.id}>
                            <td>{r.phone ?? "—"}</td>
                            <td>
                              {[r.first_name, r.last_name].filter(Boolean).join(" ") || "—"}
                            </td>
                            <td>
                              <MessageTextCell
                                preview={r.final_text_preview}
                                hasMore={Boolean(
                                  r.has_more ||
                                    r.has_long_text ||
                                    (r.final_text_preview && r.final_text_preview.split("\n").length > 3),
                                )}
                                onMore={() => void openMessageDetail(r)}
                              />
                            </td>
                            <td>{r.render_status}</td>
                            <td>
                              {sendStatusLabel(r.send_status)}
                              <div className="mmp-muted">{deliveryNote(r.send_status)}</div>
                              <div className="mmp-muted">{readNote(r.send_status)}</div>
                              {getFailureReasonFa(r.send_status, r.failure_reason) && (
                                <div
                                  style={{
                                    marginTop: "4px",
                                    fontSize: "12px",
                                    color: "#dc2626",
                                    background: "#fef2f2",
                                    border: "1px solid #fecaca",
                                    borderRadius: "6px",
                                    padding: "4px 8px",
                                  }}
                                >
                                  ⚠️ {getFailureReasonFa(r.send_status, r.failure_reason)}
                                </div>
                              )}
                            </td>
                            <td><MessageSenderCell item={r} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </TableWrap>
                )}
              </Panel>
            </>
          )}
          <ConfirmDialog
            open={archiveConfirmOpen}
            title="آرشیو کردن کمپین"
            message={
              <div className="mmp-stack">
                <p>
                  {archiveConfirmRunning
                    ? "با آرشیو کردن، ارسال‌های انجام‌نشده این کمپین متوقف می‌شوند. پیام‌هایی که قبلاً ارسال شده‌اند در تاریخچه باقی می‌مانند."
                    : "این کمپین از لیست فعال خارج می‌شود و تا زمان بازیابی قابل اجرا نخواهد بود."}
                </p>
                {archiveConfirmRunning && campaign ? (
                  <ul className="mmp-stack" style={{ margin: 0, paddingInlineStart: 20 }}>
                    <li>
                      {t("kpiMessagesSent")}: {campaign.stats.sent}
                    </li>
                    <li>
                      {t("queuePending")}: {campaign.stats.queued}
                    </li>
                    <li>
                      {t("recipients")}: {campaign.stats.total_recipients}
                    </li>
                  </ul>
                ) : null}
              </div>
            }
            confirmLabel={archiveConfirmRunning ? "توقف و آرشیو" : "آرشیو"}
            cancelLabel={t("cancel")}
            confirmLoading={archiveLoading}
            cancelDisabled={archiveLoading}
            onCancel={() => {
              if (archiveLoading) return;
              setArchiveConfirmOpen(false);
            }}
            onConfirm={() => void handleArchiveConfirmed()}
          />
          <ConfirmDialog
            open={startConfirmOpen}
            title={t("campaignControlledConfirmTitle")}
            message={
              <div className="mmp-stack">
                <p>{t("campaignControlledConfirmBody")}</p>
                {preflight ? (
                  <ul className="mmp-stack" style={{ margin: 0, paddingInlineStart: 20 }}>
                    <li>
                      {t("campaignControlledConfirmRecipients")}:{" "}
                      {preflight.total_recipients ?? preflight.total_messages}
                    </li>
                    <li>
                      {t("campaignControlledConfirmPrepared")}:{" "}
                      {preflight.prepared_messages ?? preflight.ready_messages}
                    </li>
                    <li>
                      {t("campaignControlledConfirmSenders")}:{" "}
                      {preflight.execution_usable_accounts ??
                        preflight.usable_accounts ??
                        0}
                      /{preflight.assigned_accounts}
                    </li>
                    <li>
                      {t("campaignControlledConfirmMaxMessages")}:{" "}
                      {t("campaignUnlimitedTotalCap")}
                    </li>
                  </ul>
                ) : null}
              </div>
            }
            confirmLabel={t("campaignControlledConfirmAction")}
            cancelLabel={t("campaignControlledConfirmCancel")}
            confirmLoading={startConfirmLoading}
            cancelDisabled={startConfirmLoading}
            onCancel={() => {
              if (startConfirmLoading) return;
              setStartConfirmOpen(false);
            }}
            onConfirm={() => void handleStartConfirmed(true)}
          />
          <MessageDetailModal
            open={detailOpen}
            loading={detailLoading}
            error={detailError}
            detail={detail}
            onClose={() => {
              setDetailOpen(false);
              setDetail(null);
              setDetailError(null);
            }}
          />
        </PageContent>
      </Layout>
    </>
  );
}
