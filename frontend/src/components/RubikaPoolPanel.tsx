import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState, FormField, inputClassName, selectClassName, tableClassName, TableWrap } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  fetchRubikaAccounts,
  fetchRubikaSchedule,
  removeRubikaPoolMembership,
  restoreRubikaAccount,
  updateRubikaSchedule,
  upsertRubikaPool,
} from "@/lib/rubika-api";
import type { RubikaPoolAccountItem, RubikaPoolPhase, RubikaScheduleItem } from "@/types/rubika";
import { toJalaliDateTime } from "@/utils/jalali";

const PHASES: RubikaPoolPhase[] = ["day", "night", "listener", "status"];

function statusColor(status: string): string {
  if (status === "active") return "#166534";
  if (status === "resting") return "#b45309";
  if (status === "banned") return "#991b1b";
  return "#6b7280";
}

const badgeStyle = (color: string): React.CSSProperties => ({
  display: "inline-block",
  padding: "3px 8px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: color,
  color: "#fff",
});

type RubikaPoolPanelProps = {
  canManage: boolean;
};

export function RubikaPoolPanel({ canManage }: RubikaPoolPanelProps) {
  const { t } = useTranslation();
  const [accounts, setAccounts] = useState<RubikaPoolAccountItem[]>([]);
  const [schedule, setSchedule] = useState<RubikaScheduleItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [phaseChoice, setPhaseChoice] = useState<Record<number, RubikaPoolPhase>>({});
  const [priorityChoice, setPriorityChoice] = useState<Record<number, number>>({});
  const [busyAccountId, setBusyAccountId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [accountsResult, scheduleResult] = await Promise.all([
        fetchRubikaAccounts(),
        fetchRubikaSchedule(),
      ]);
      setAccounts(accountsResult.items);
      setSchedule(scheduleResult.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("rubikaLoadError"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleAssign(accountId: number) {
    const phase = phaseChoice[accountId] ?? "day";
    const priority = priorityChoice[accountId] ?? 1;
    setBusyAccountId(accountId);
    setError(null);
    setNotice(null);
    try {
      const result = await upsertRubikaPool(accountId, { phase, priority });
      setNotice(result.message);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setBusyAccountId(null);
    }
  }

  async function handleRemove(accountId: number, phase: RubikaPoolPhase) {
    setBusyAccountId(accountId);
    setError(null);
    setNotice(null);
    try {
      await removeRubikaPoolMembership(accountId, phase);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setBusyAccountId(null);
    }
  }

  async function handleRestore(accountId: number) {
    setBusyAccountId(accountId);
    setError(null);
    setNotice(null);
    try {
      const result = await restoreRubikaAccount(accountId);
      setNotice(result.message);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setBusyAccountId(null);
    }
  }

  const [scheduleDraft, setScheduleDraft] = useState<Record<string, RubikaScheduleItem>>({});

  function draftFor(phase: "day" | "night"): RubikaScheduleItem {
    if (scheduleDraft[phase]) return scheduleDraft[phase];
    const existing = schedule.find((s) => s.phase === phase);
    return (
      existing ?? {
        phase,
        start_hour: phase === "day" ? 8 : 22,
        end_hour: phase === "day" ? 22 : 8,
        max_per_hour: 50,
        is_active: true,
      }
    );
  }

  function updateDraft(phase: "day" | "night", patch: Partial<RubikaScheduleItem>) {
    setScheduleDraft((cur) => ({ ...cur, [phase]: { ...draftFor(phase), ...patch } }));
  }

  async function handleScheduleSave(item: RubikaScheduleItem) {
    setError(null);
    setNotice(null);
    try {
      await updateRubikaSchedule(item.phase, {
        start_hour: item.start_hour,
        end_hour: item.end_hour,
        max_per_hour: item.max_per_hour,
        is_active: item.is_active,
      });
      setScheduleDraft((cur) => {
        const next = { ...cur };
        delete next[item.phase];
        return next;
      });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    }
  }

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {error ? <Alert variant="error">{error}</Alert> : null}
      {notice ? <Alert variant="success">{notice}</Alert> : null}

      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <strong>{t("rubikaPoolTitle")}</strong>
          <Button type="button" size="sm" onClick={() => void load()} disabled={loading}>
            {loading ? t("loading") : t("refresh")}
          </Button>
        </div>

        {loading && accounts.length === 0 ? (
          <EmptyState>{t("loading")}</EmptyState>
        ) : accounts.length === 0 ? (
          <EmptyState>{t("rubikaPoolEmpty")}</EmptyState>
        ) : (
          <TableWrap>
            <table className={tableClassName}>
              <thead>
                <tr>
                  <th>{t("rubikaPoolColAccount")}</th>
                  <th>{t("rubikaPoolColStatus")}</th>
                  <th>{t("rubikaPoolColPhase")}</th>
                  <th>{t("rubikaPoolColPriority")}</th>
                  <th>{t("rubikaPoolColLastUsed")}</th>
                  <th>{t("rubikaPoolColLastError")}</th>
                  {canManage ? <th>{t("actions")}</th> : null}
                </tr>
              </thead>
              <tbody>
                {accounts.map((acc) => (
                  <tr key={`${acc.account_id}-${acc.phase}`}>
                    <td>
                      {acc.label ?? `#${acc.account_id}`}
                      <div style={{ fontSize: 12, opacity: 0.7, direction: "ltr", textAlign: "left" }}>
                        {acc.phone_number ?? "—"}
                      </div>
                    </td>
                    <td>
                      <span style={badgeStyle(statusColor(acc.account_status))}>
                        {t(`accountStatus_${acc.account_status}` as never, {
                          defaultValue: acc.account_status,
                        })}
                      </span>
                    </td>
                    <td>{t(`rubikaPhase_${acc.phase}` as never, { defaultValue: acc.phase })}</td>
                    <td>{acc.priority || "—"}</td>
                    <td>{acc.last_used_at ? toJalaliDateTime(acc.last_used_at) : "—"}</td>
                    <td style={{ fontSize: 12, color: acc.last_error_message ? "#991b1b" : undefined }}>
                      {acc.last_error_message ?? "—"}
                    </td>
                    {canManage ? (
                      <td>
                        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                          <select
                            className={selectClassName}
                            value={phaseChoice[acc.account_id] ?? "day"}
                            onChange={(e) =>
                              setPhaseChoice((cur) => ({
                                ...cur,
                                [acc.account_id]: e.target.value as RubikaPoolPhase,
                              }))
                            }
                          >
                            {PHASES.map((p) => (
                              <option key={p} value={p}>
                                {t(`rubikaPhase_${p}` as never, { defaultValue: p })}
                              </option>
                            ))}
                          </select>
                          <input
                            className={inputClassName}
                            type="number"
                            min={1}
                            max={100}
                            style={{ width: 64 }}
                            value={priorityChoice[acc.account_id] ?? 1}
                            onChange={(e) =>
                              setPriorityChoice((cur) => ({
                                ...cur,
                                [acc.account_id]: Number(e.target.value) || 1,
                              }))
                            }
                          />
                          <Button
                            type="button"
                            size="sm"
                            disabled={busyAccountId === acc.account_id}
                            onClick={() => void handleAssign(acc.account_id)}
                          >
                            {t("rubikaPoolAssign")}
                          </Button>
                          {acc.phase !== "unassigned" ? (
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              disabled={busyAccountId === acc.account_id}
                              onClick={() => void handleRemove(acc.account_id, acc.phase as RubikaPoolPhase)}
                            >
                              {t("rubikaPoolRemove")}
                            </Button>
                          ) : null}
                          {acc.account_status === "resting" ? (
                            <Button
                              type="button"
                              size="sm"
                              disabled={busyAccountId === acc.account_id}
                              onClick={() => void handleRestore(acc.account_id)}
                            >
                              {t("rubikaPoolRestore")}
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </div>

      <div>
        <strong style={{ display: "block", marginBottom: 8 }}>{t("rubikaScheduleTitle")}</strong>
        <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
          {(["day", "night"] as const).map((phase) => {
            const draft = draftFor(phase);
            return (
              <div
                key={phase}
                style={{
                  border: "1px solid rgba(0,0,0,0.1)",
                  borderRadius: 10,
                  padding: 12,
                  display: "grid",
                  gap: 8,
                }}
              >
                <strong>{t(`rubikaPhase_${phase}`)}</strong>
                <div style={{ display: "flex", gap: 8 }}>
                  <FormField label={t("rubikaScheduleStart")}>
                    <input
                      className={inputClassName}
                      type="number"
                      min={0}
                      max={24}
                      value={draft.start_hour}
                      disabled={!canManage}
                      onChange={(e) => updateDraft(phase, { start_hour: Number(e.target.value) })}
                    />
                  </FormField>
                  <FormField label={t("rubikaScheduleEnd")}>
                    <input
                      className={inputClassName}
                      type="number"
                      min={0}
                      max={24}
                      value={draft.end_hour}
                      disabled={!canManage}
                      onChange={(e) => updateDraft(phase, { end_hour: Number(e.target.value) })}
                    />
                  </FormField>
                </div>
                <FormField label={t("rubikaScheduleMaxPerHour")}>
                  <input
                    className={inputClassName}
                    type="number"
                    min={1}
                    max={1000}
                    value={draft.max_per_hour}
                    disabled={!canManage}
                    onChange={(e) => updateDraft(phase, { max_per_hour: Number(e.target.value) })}
                  />
                </FormField>
                {canManage ? (
                  <Button type="button" size="sm" onClick={() => void handleScheduleSave(draft)}>
                    {t("save")}
                  </Button>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
