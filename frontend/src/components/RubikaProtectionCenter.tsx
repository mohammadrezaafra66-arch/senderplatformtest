import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  Alert,
  Button,
  EmptyState,
  StatCard,
  TableWrap,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  acknowledgeRubikaAlert,
  acknowledgeRubikaIncident,
  fetchRubikaProtectionAccountDetail,
  fetchRubikaProtectionOverview,
  restoreRubikaProtectionAccount,
} from "@/lib/rubika-api";
import type {
  RubikaAlertItem,
  RubikaIncidentItem,
  RubikaProtectionAccountRow,
  RubikaProtectionOverview,
} from "@/types/rubika";
import { toJalaliDateTime } from "@/utils/jalali";

const REFRESH_MS = 30_000;
const UNKNOWN = "نامشخص";

function display(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return UNKNOWN;
  return String(value);
}

function healthLabel(state: string | null | undefined, t: (k: string) => string): string {
  switch ((state || "").toLowerCase()) {
    case "healthy":
      return t("rubikaHealthHealthy");
    case "degraded":
      return t("rubikaHealthDegraded");
    case "throttled":
      return t("rubikaHealthThrottled");
    case "quarantined":
      return t("rubikaHealthQuarantined");
    case "critical":
      return t("rubikaHealthCritical");
    case "offline":
      return t("rubikaHealthOffline");
    default:
      return UNKNOWN;
  }
}

function healthColor(state: string | null | undefined): string {
  switch ((state || "").toLowerCase()) {
    case "healthy":
      return "#166534";
    case "degraded":
      return "#b45309";
    case "throttled":
      return "#c2410c";
    case "quarantined":
      return "#991b1b";
    case "critical":
      return "#7f1d1d";
    case "offline":
      return "#4b5563";
    default:
      return "#6b7280";
  }
}

function circuitLabel(state: string | null | undefined, t: (k: string) => string): string {
  switch ((state || "").toLowerCase()) {
    case "closed":
      return t("rubikaCircuitClosed");
    case "open":
      return t("rubikaCircuitOpen");
    case "half_open":
      return t("rubikaCircuitHalfOpen");
    default:
      return UNKNOWN;
  }
}

function severityColor(severity: string): string {
  const s = severity.toUpperCase();
  if (s === "CRITICAL") return "#991b1b";
  if (s === "WARNING" || s === "HIGH") return "#b45309";
  return "#1d4ed8";
}

const badgeStyle = (color: string): CSSProperties => ({
  display: "inline-block",
  padding: "3px 8px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: color,
  color: "#fff",
});

type Props = {
  canManage: boolean;
};

export function RubikaProtectionCenter({ canManage }: Props) {
  const { t } = useTranslation();
  const [data, setData] = useState<RubikaProtectionOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [restoreTarget, setRestoreTarget] = useState<RubikaProtectionAccountRow | null>(null);
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [incidentScope, setIncidentScope] = useState<string>("");
  const [incidentSeverity, setIncidentSeverity] = useState<string>("");
  const [incidentSort, setIncidentSort] = useState<string>("newest");
  const [selectedIncident, setSelectedIncident] = useState<RubikaIncidentItem | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const overview = await fetchRubikaProtectionOverview();
      if (!mountedRef.current) return;
      setData(overview);
    } catch (err) {
      if (!mountedRef.current) return;
      setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    mountedRef.current = true;
    const boot = window.setTimeout(() => {
      void load();
    }, 0);
    const timer = window.setInterval(() => {
      void load();
    }, REFRESH_MS);
    return () => {
      mountedRef.current = false;
      window.clearTimeout(boot);
      window.clearInterval(timer);
    };
  }, [load]);

  const openDetail = useCallback(
    async (accountId: number) => {
      setSelectedId(accountId);
      setDetailLoading(true);
      setDetail(null);
      try {
        const result = await fetchRubikaProtectionAccountDetail(accountId);
        if (mountedRef.current) setDetail(result);
      } catch (err) {
        if (mountedRef.current) {
          setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
        }
      } finally {
        if (mountedRef.current) setDetailLoading(false);
      }
    },
    [t],
  );

  async function confirmRestore() {
    if (!restoreTarget || !canManage) return;
    setRestoreBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await restoreRubikaProtectionAccount(restoreTarget.account_id);
      setNotice(result.message);
      setRestoreTarget(null);
      await load();
      if (selectedId === restoreTarget.account_id) {
        await openDetail(restoreTarget.account_id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRestoreBusy(false);
    }
  }

  async function onAckIncident(incidentId: string) {
    if (!canManage) return;
    try {
      await acknowledgeRubikaIncident(incidentId);
      setNotice(t("rubikaIncidentAckOk"));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  async function onAckAlert(dedupeKey: string) {
    if (!canManage) return;
    try {
      await acknowledgeRubikaAlert(dedupeKey);
      setNotice(t("rubikaAlertAckOk"));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  const filteredIncidents = useMemo(() => {
    const items = data?.incidents ?? [];
    let next = items;
    if (incidentScope) {
      next = next.filter((i) => i.scope.toUpperCase() === incidentScope.toUpperCase());
    }
    if (incidentSeverity) {
      next = next.filter((i) => i.severity.toUpperCase() === incidentSeverity.toUpperCase());
    }
    if (incidentSort === "severity") {
      const rank: Record<string, number> = { CRITICAL: 0, WARNING: 1, HIGH: 1, INFO: 2 };
      next = [...next].sort(
        (a, b) => (rank[a.severity.toUpperCase()] ?? 9) - (rank[b.severity.toUpperCase()] ?? 9),
      );
    } else if (incidentSort === "occurrence_count") {
      next = [...next].sort((a, b) => b.occurrence_count - a.occurrence_count);
    } else {
      next = [...next].sort((a, b) => String(b.opened_at).localeCompare(String(a.opened_at)));
    }
    return next;
  }, [data?.incidents, incidentScope, incidentSeverity, incidentSort]);

  const circuitState = data?.system.circuit.state ?? "unknown";
  const criticalAlerts = (data?.alerts ?? []).filter(
    (a) => a.severity.toUpperCase() === "CRITICAL" && a.status !== "RESOLVED",
  );

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {criticalAlerts.length > 0 || circuitState === "open" ? (
        <Alert variant="error">
          {circuitState === "open"
            ? t("rubikaCircuitOpenBanner")
            : criticalAlerts[0]?.message || t("rubikaCriticalAlertBanner")}
          {data?.summary.critical_alerts
            ? ` — ${t("rubikaAlertBadge", { count: data.summary.critical_alerts })}`
            : null}
        </Alert>
      ) : null}

      {error ? <Alert variant="error">{error}</Alert> : null}
      {notice ? <Alert variant="success">{notice}</Alert> : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Button type="button" onClick={() => void load()} disabled={loading}>
          {t("rubikaProtectionRefresh")}
        </Button>
        <span style={{ fontSize: 12, color: "#6b7280" }}>
          {data?.evaluated_at
            ? `${t("rubikaProtectionLastRefresh")}: ${toJalaliDateTime(data.evaluated_at)}`
            : null}
        </span>
        {(data?.summary.unresolved_alerts ?? 0) > 0 ? (
          <span style={badgeStyle("#991b1b")}>
            {t("rubikaAlertBadge", { count: data?.summary.unresolved_alerts ?? 0 })}
          </span>
        ) : null}
      </div>

      {loading && !data ? <EmptyState>{t("loading")}</EmptyState> : null}

      {data ? (
        <>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: 10,
            }}
          >
            <StatCard label={t("rubikaSummaryTotal")} value={data.summary.total_accounts} />
            <StatCard label={t("rubikaSummaryReady")} value={data.summary.ready} />
            <StatCard label={t("rubikaSummaryHealthy")} value={data.summary.healthy} />
            <StatCard label={t("rubikaSummaryDegraded")} value={data.summary.degraded} />
            <StatCard label={t("rubikaSummaryThrottled")} value={data.summary.throttled} />
            <StatCard label={t("rubikaSummaryQuarantined")} value={data.summary.quarantined} />
            <StatCard label={t("rubikaSummaryRequiresLogin")} value={data.summary.requires_login} />
            <StatCard label={t("rubikaSummaryOpenIncidents")} value={data.summary.open_incidents} />
            <StatCard
              label={t("rubikaSummaryCriticalIncidents")}
              value={data.summary.critical_incidents}
            />
            <StatCard
              label={t("rubikaSummaryCircuit")}
              value={circuitLabel(data.summary.circuit_state, t)}
            />
          </div>

          <section
            style={{
              border: "1px solid rgba(0,0,0,0.1)",
              borderRadius: 12,
              padding: 14,
              background: circuitState === "open" ? "rgba(153,27,27,0.08)" : "transparent",
            }}
          >
            <h3 style={{ margin: "0 0 8px" }}>{t("rubikaSystemProtectionTitle")}</h3>
            {circuitState === "open" ? (
              <Alert variant="error">{t("rubikaCircuitOpenBanner")}</Alert>
            ) : null}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
                gap: 8,
                marginTop: 8,
              }}
            >
              <div>
                <strong>{t("rubikaCircuitState")}: </strong>
                <span style={badgeStyle(circuitState === "open" ? "#991b1b" : "#166534")}>
                  {circuitLabel(circuitState, t)}
                </span>
              </div>
              <div>
                <strong>{t("rubikaCircuitOpenedAt")}: </strong>
                {display(
                  data.system.circuit.opened_at
                    ? toJalaliDateTime(data.system.circuit.opened_at)
                    : null,
                )}
              </div>
              <div>
                <strong>{t("rubikaCircuitOpenUntil")}: </strong>
                {display(
                  data.system.circuit.open_until
                    ? toJalaliDateTime(data.system.circuit.open_until)
                    : null,
                )}
              </div>
              <div>
                <strong>{t("rubikaCircuitReason")}: </strong>
                {display(data.system.circuit.reason)}
              </div>
              <div>
                <strong>{t("rubikaCircuitProbe")}: </strong>
                {display(data.system.circuit.probe_remaining)} /{" "}
                {display(data.system.circuit.probe_budget)}
              </div>
              <div>
                <strong>{t("rubikaCircuitAccounts")}: </strong>
                {display(data.system.circuit.systemic_account_count)}
              </div>
              <div>
                <strong>{t("rubikaSummaryOpenIncidents")}: </strong>
                {display(data.system.open_incidents)}
              </div>
            </div>
            <p style={{ fontSize: 12, color: "#6b7280", marginTop: 10 }}>
              {data.operator_notes?.circuit_force_close_reason || t("rubikaCircuitNoForceClose")}
            </p>
          </section>

          {(data.alerts ?? []).length > 0 ? (
            <section>
              <h3 style={{ margin: "0 0 8px" }}>{t("rubikaAlertsTitle")}</h3>
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 8 }}>
                {data.alerts.map((alert: RubikaAlertItem) => (
                  <li
                    key={alert.alert_id}
                    style={{
                      border: "1px solid rgba(0,0,0,0.08)",
                      borderRadius: 10,
                      padding: 10,
                      background:
                        alert.severity.toUpperCase() === "CRITICAL"
                          ? "rgba(153,27,27,0.06)"
                          : "transparent",
                    }}
                  >
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                      <span style={badgeStyle(severityColor(alert.severity))}>{alert.severity}</span>
                      <strong>{alert.title}</strong>
                      <span style={{ fontSize: 12, color: "#6b7280" }}>
                        ×{alert.occurrence_count}
                      </span>
                      {canManage && alert.status === "OPEN" ? (
                        <Button size="sm" type="button" onClick={() => void onAckAlert(alert.dedupe_key)}>
                          {t("rubikaAcknowledge")}
                        </Button>
                      ) : null}
                    </div>
                    <div style={{ marginTop: 4 }}>{alert.message}</div>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <section>
            <h3 style={{ margin: "0 0 8px" }}>{t("rubikaCampaignImpactTitle")}</h3>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))",
                gap: 8,
              }}
            >
              <StatCard
                label={t("rubikaImpactCampaigns")}
                value={data.campaign_impact.campaigns_affected_by_quarantine}
              />
              <StatCard
                label={t("rubikaImpactPending")}
                value={data.campaign_impact.pending_messages_blocked}
              />
              <StatCard
                label={t("rubikaImpactRetryable")}
                value={data.campaign_impact.retryable_blocked_messages}
              />
              <StatCard
                label={t("rubikaImpactCircuit")}
                value={data.campaign_impact.messages_waiting_circuit_open}
              />
            </div>
          </section>

          <section>
            <h3 style={{ margin: "0 0 8px" }}>{t("rubikaAccountProtectionTitle")}</h3>
            {data.accounts.length === 0 ? (
              <EmptyState>{t("rubikaProtectionNoAccounts")}</EmptyState>
            ) : (
              <TableWrap>
                <table className={tableClassName}>
                  <thead>
                    <tr>
                      <th>{t("rubikaColAccountId")}</th>
                      <th>{t("rubikaColLabel")}</th>
                      <th>{t("rubikaColDelivery")}</th>
                      <th>{t("rubikaColStatus")}</th>
                      <th>{t("rubikaColReadiness")}</th>
                      <th>{t("rubikaColPreflight")}</th>
                      <th>{t("rubikaColLifecycle")}</th>
                      <th>{t("rubikaColHealth")}</th>
                      <th>{t("rubikaColPool")}</th>
                      <th>{t("rubikaColQuota")}</th>
                      <th>{t("rubikaColCooldown")}</th>
                      <th>{t("rubikaColLastFailure")}</th>
                      <th>{t("rubikaColQuarantine")}</th>
                      <th>{t("rubikaColAction")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.accounts.map((row) => (
                      <tr key={row.account_id}>
                        <td>
                          <button
                            type="button"
                            onClick={() => void openDetail(row.account_id)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "#1d4ed8",
                              cursor: "pointer",
                              textDecoration: "underline",
                            }}
                          >
                            {row.account_id}
                          </button>
                        </td>
                        <td>
                          {display(row.label)}
                          <div style={{ fontSize: 11, color: "#6b7280" }}>
                            {display(row.phone_number)}
                          </div>
                        </td>
                        <td>{display(row.delivery_mode)}</td>
                        <td>{display(row.account_status)}</td>
                        <td>
                          {row.session_ready ? t("rubikaSessionReady") : t("rubikaSessionNotReady")}
                          <div style={{ fontSize: 11 }}>{display(row.session_code)}</div>
                        </td>
                        <td>{display(row.preflight?.label || row.preflight?.code)}</td>
                        <td>{display(row.lifecycle_state)}</td>
                        <td>
                          <span style={badgeStyle(healthColor(row.health_state))}>
                            {healthLabel(row.health_state, t)}
                          </span>
                        </td>
                        <td>
                          {display(row.pool_phase)} / {display(row.priority)}
                        </td>
                        <td style={{ fontSize: 12 }}>
                          {row.sent_today}/{row.daily_cap} · {row.sent_this_hour}/{row.hourly_cap}
                          <div>
                            {t("rubikaRemaining")}: {row.remaining_daily}/{row.remaining_hourly}
                          </div>
                        </td>
                        <td>
                          {row.cooldown_until
                            ? toJalaliDateTime(row.cooldown_until)
                            : UNKNOWN}
                        </td>
                        <td style={{ fontSize: 12 }}>
                          {display(row.last_failure_code)}
                          <div>
                            {row.last_failure_at
                              ? toJalaliDateTime(row.last_failure_at)
                              : UNKNOWN}
                          </div>
                        </td>
                        <td>
                          {row.quarantined
                            ? display(row.quarantine_reason)
                            : t("rubikaNotQuarantined")}
                        </td>
                        <td>
                          {canManage && row.restore?.allowed ? (
                            <Button
                              size="sm"
                              type="button"
                              onClick={() => setRestoreTarget(row)}
                            >
                              {t("rubikaRestoreAction")}
                            </Button>
                          ) : (
                            <span style={{ fontSize: 12, color: "#6b7280" }}>
                              {display(row.restore?.reason)}
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>

          {selectedId != null ? (
            <section
              style={{
                border: "1px solid rgba(0,0,0,0.1)",
                borderRadius: 12,
                padding: 14,
                background: "rgba(29,78,216,0.03)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <h3 style={{ margin: 0 }}>
                  {t("rubikaAccountDetailTitle")} #{selectedId}
                </h3>
                <Button size="sm" type="button" onClick={() => setSelectedId(null)}>
                  {t("rubikaCloseDetail")}
                </Button>
              </div>
              {detailLoading ? <EmptyState>{t("loading")}</EmptyState> : null}
              {detail ? (
                <div style={{ display: "grid", gap: 10, marginTop: 10, fontSize: 13 }}>
                  <DetailBlock title={t("rubikaDetailIdentity")}>
                    {JSON.stringify(detail.identity ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailSession")}>
                    {JSON.stringify(detail.session ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailLifecycle")}>
                    {JSON.stringify(detail.lifecycle ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailQuota")}>
                    {JSON.stringify(detail.quota ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailHealth")}>
                    {JSON.stringify(detail.health ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailFailures")}>
                    {JSON.stringify(detail.recent_failures ?? [], null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailIncidents")}>
                    {JSON.stringify(detail.open_incidents ?? [], null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailEvents")}>
                    {JSON.stringify(detail.protection_events ?? [], null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailCampaigns")}>
                    {JSON.stringify(detail.campaign_usage ?? {}, null, 0)}
                  </DetailBlock>
                  <DetailBlock title={t("rubikaDetailActions")}>
                    {JSON.stringify(detail.actions ?? {}, null, 0)}
                  </DetailBlock>
                </div>
              ) : null}
            </section>
          ) : null}

          <section>
            <h3 style={{ margin: "0 0 8px" }}>{t("rubikaIncidentCenterTitle")}</h3>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
              <select
                className={selectClassName}
                value={incidentScope}
                onChange={(e) => setIncidentScope(e.target.value)}
              >
                <option value="">{t("rubikaFilterAllScopes")}</option>
                <option value="ACCOUNT">ACCOUNT</option>
                <option value="SYSTEM">SYSTEM</option>
              </select>
              <select
                className={selectClassName}
                value={incidentSeverity}
                onChange={(e) => setIncidentSeverity(e.target.value)}
              >
                <option value="">{t("rubikaFilterAllSeverities")}</option>
                <option value="CRITICAL">CRITICAL</option>
                <option value="WARNING">WARNING</option>
                <option value="INFO">INFO</option>
              </select>
              <select
                className={selectClassName}
                value={incidentSort}
                onChange={(e) => setIncidentSort(e.target.value)}
              >
                <option value="newest">{t("rubikaSortNewest")}</option>
                <option value="severity">{t("rubikaSortSeverity")}</option>
                <option value="occurrence_count">{t("rubikaSortOccurrence")}</option>
              </select>
            </div>
            {filteredIncidents.length === 0 ? (
              <EmptyState>{t("rubikaNoIncidents")}</EmptyState>
            ) : (
              <TableWrap>
                <table className={tableClassName}>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>{t("rubikaColScope")}</th>
                      <th>{t("rubikaColAccountId")}</th>
                      <th>{t("rubikaColCategory")}</th>
                      <th>{t("rubikaColSeverity")}</th>
                      <th>{t("rubikaColStatus")}</th>
                      <th>{t("rubikaColOpened")}</th>
                      <th>{t("rubikaColLastSeen")}</th>
                      <th>{t("rubikaColCount")}</th>
                      <th>{t("rubikaColReason")}</th>
                      <th>{t("rubikaColAction")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredIncidents.map((inc) => (
                      <tr key={inc.incident_id}>
                        <td>
                          <button
                            type="button"
                            onClick={() => setSelectedIncident(inc)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "#1d4ed8",
                              cursor: "pointer",
                              textDecoration: "underline",
                            }}
                          >
                            {inc.incident_id}
                          </button>
                        </td>
                        <td>{inc.scope}</td>
                        <td>{display(inc.account_id)}</td>
                        <td>{inc.category}</td>
                        <td>
                          <span style={badgeStyle(severityColor(inc.severity))}>{inc.severity}</span>
                        </td>
                        <td>{inc.status}</td>
                        <td>{inc.opened_at ? toJalaliDateTime(inc.opened_at) : UNKNOWN}</td>
                        <td>{inc.last_seen_at ? toJalaliDateTime(inc.last_seen_at) : UNKNOWN}</td>
                        <td>{inc.occurrence_count}</td>
                        <td>{display(inc.reason)}</td>
                        <td>
                          {canManage ? (
                            <Button
                              size="sm"
                              type="button"
                              onClick={() => void onAckIncident(inc.incident_id)}
                            >
                              {t("rubikaAcknowledge")}
                            </Button>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
            {selectedIncident ? (
              <div
                style={{
                  marginTop: 10,
                  padding: 12,
                  borderRadius: 10,
                  border: "1px solid rgba(0,0,0,0.1)",
                }}
              >
                <h4 style={{ marginTop: 0 }}>{t("rubikaIncidentDetailTitle")}</h4>
                <pre style={{ whiteSpace: "pre-wrap", fontSize: 12, margin: 0 }}>
                  {JSON.stringify(
                    {
                      ...selectedIncident,
                      related_circuit_state: circuitState,
                      resolution_status: selectedIncident.status,
                    },
                    null,
                    2,
                  )}
                </pre>
              </div>
            ) : null}
          </section>

          <section>
            <h3 style={{ margin: "0 0 8px" }}>{t("rubikaEventTimelineTitle")}</h3>
            {(data.events ?? []).length === 0 ? (
              <EmptyState>{t("rubikaNoEvents")}</EmptyState>
            ) : (
              <TableWrap>
                <table className={tableClassName}>
                  <thead>
                    <tr>
                      <th>{t("rubikaColTime")}</th>
                      <th>{t("rubikaColScope")}</th>
                      <th>{t("rubikaColAccountId")}</th>
                      <th>{t("rubikaColEvent")}</th>
                      <th>{t("rubikaColReason")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.events.map((ev, idx) => (
                      <tr key={`${ev.event}-${ev.time}-${idx}`}>
                        <td>{ev.time ? toJalaliDateTime(ev.time) : UNKNOWN}</td>
                        <td>{display(ev.scope)}</td>
                        <td>{display(ev.account_id)}</td>
                        <td>{display(ev.event)}</td>
                        <td>{display(ev.reason)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            )}
          </section>
        </>
      ) : null}

      <ConfirmDialog
        open={restoreTarget != null}
        title={t("rubikaRestoreConfirmTitle")}
        message={
          restoreTarget ? (
            <div style={{ display: "grid", gap: 6 }}>
              <div>{t("rubikaRestoreConfirmMessage")}</div>
              <div>
                {t("rubikaColAccountId")}: {restoreTarget.account_id}
              </div>
              <div>
                {t("rubikaColHealth")}: {healthLabel(restoreTarget.health_state, t)}
              </div>
              <div>
                {t("rubikaColQuarantine")}: {display(restoreTarget.quarantine_reason)}
              </div>
              <div>
                {t("rubikaColReadiness")}:{" "}
                {restoreTarget.session_ready
                  ? t("rubikaSessionReady")
                  : t("rubikaSessionNotReady")}
              </div>
            </div>
          ) : null
        }
        confirmLabel={t("rubikaRestoreAction")}
        cancelLabel={t("cancel")}
        confirmLoading={restoreBusy}
        onConfirm={() => void confirmRestore()}
        onCancel={() => setRestoreTarget(null)}
      />
    </div>
  );
}

function DetailBlock({ title, children }: { title: string; children: string }) {
  return (
    <div>
      <strong>{title}</strong>
      <pre
        style={{
          margin: "4px 0 0",
          padding: 8,
          background: "rgba(0,0,0,0.03)",
          borderRadius: 8,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12,
        }}
      >
        {children}
      </pre>
    </div>
  );
}
