import Head from "next/head";
import Link from "next/link";
import { useRouter } from "next/router";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { MessageSenderCell } from "@/components/MessageSenderCell";
import { MessageTextCell } from "@/components/MessageTextCell";
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
import { getFailureReasonFa } from "@/lib/failure-reason";
import {
  fetchCampaignDetail,
  fetchCampaignPreflight,
  fetchCampaignRecipientDetail,
  fetchCampaignRecipients,
  startCampaign,
  stopCampaign,
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
import { accountDisplayName, orderedSenders } from "@/utils/sender-accounts";

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
  }, [campaignId, canView, sendStatusFilter, t]);

  useEffect(() => {
    if (!router.isReady) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial and filter-driven remote data load
    void loadCampaign();
  }, [router.isReady, loadCampaign]);

  const startBlocked = Boolean(preflight && !preflight.allowed_to_start);
  const safetyLabel = (() => {
    const state = preflight?.execution_safety_state;
    if (state === "BLOCKED" || state === "PAUSED_SAFETY") return t("campaignSafetyBlocked");
    if (state === "CAPACITY_WARNING" || state === "WAITING_WINDOW" || state === "WAITING_CAPACITY") {
      return t("campaignSafetyWarning");
    }
    return t("campaignSafetyReady");
  })();

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
      await updateCampaignAccounts(campaignId, senderMode === "auto" ? [] : selectedAccountIds);
      setNotice(t("senderAccountsSaved"));
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
              <div className="mmp-stack" style={{ marginTop: 16 }}>
                <Button
                  type="button"
                  variant="primary"
                  disabled={!canControl || actionLoading || startBlocked}
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

              {preflightError ? <Alert>{preflightError}</Alert> : null}
              {startBlocked && preflight ? (
                <Alert>
                  {preflight.blockers[0]?.message || preflight.message || t("campaignStartBlocked")}
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
                        {t("campaignUsableAccounts")}: {preflight.usable_accounts}/{preflight.assigned_accounts}
                      </div>
                      <div>
                        {t("campaignBlockedAccounts")}: {preflight.blocked_accounts}
                      </div>
                      <div>
                        {t("campaignTodayCapacity")}: {preflight.estimated_today_capacity ?? t("nA")}
                      </div>
                      <div>
                        {t("campaignHourlyCapacity")}: {preflight.estimated_hourly_capacity ?? t("nA")}
                      </div>
                      <div>
                        {t("campaignImmediateCapacity")}: {preflight.immediate_capacity ?? t("nA")}
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
                              <th>{t("campaignCapacityTableReady")}</th>
                              <th>{t("campaignCapacityTableDaily")}</th>
                              <th>{t("campaignCapacityTableHourly")}</th>
                              <th>{t("campaignCapacityTableNext")}</th>
                              <th>{t("campaignCapacityTableStatus")}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {preflight.accounts.map((row) => (
                              <tr key={row.account_id}>
                                <td>{row.label || `#${row.account_id}`}</td>
                                <td>{row.assigned}</td>
                                <td>{row.health ?? "—"}</td>
                                <td>{row.readiness ?? "—"}</td>
                                <td>{row.daily_remaining ?? "—"}</td>
                                <td>{row.hourly_remaining ?? "—"}</td>
                                <td>
                                  {row.next_allowed_at
                                    ? new Date(row.next_allowed_at).toLocaleString("fa-IR")
                                    : "—"}
                                </td>
                                <td>
                                  {row.eligible_now
                                    ? t("campaignSafetyReady")
                                    : row.block_code || t("campaignSafetyBlocked")}
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
                          {sender.label && sender.account_identifier ? <span>{sender.account_identifier}</span> : null}
                          <span>{t(`account_status_${sender.status}`)}</span>
                        </li>
                      ))}
                    </ol>
                  ) : null}
                  {canControl ? (
                    <div style={{ marginTop: 16 }}>
                      <CampaignSenderSelector
                        platform={campaign.platform}
                        accounts={accounts}
                        mode={senderMode}
                        selectedIds={selectedAccountIds}
                        loading={accountsLoading}
                        loadError={accountsError}
                        disabled={senderSaving}
                        onModeChange={setSenderMode}
                        onSelectedIdsChange={setSelectedAccountIds}
                        onRetry={() => void loadAccounts()}
                      />
                      <p className="mmp-muted">{t("senderChangeWarning")}</p>
                      <Button
                        variant="primary"
                        disabled={senderSaving || (senderMode === "manual" && selectedAccountIds.length === 0)}
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
                              {r.send_status}
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
