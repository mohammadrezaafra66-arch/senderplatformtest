import type { TFunction } from "i18next";

import type { CampaignPreflight, CampaignPreflightAccount } from "@/types/campaign";

const RAW_ENUM_PATTERN = /^[A-Z][A-Z0-9_]+$/;

export function isRawBackendEnum(value: string | null | undefined): boolean {
  if (!value?.trim()) return false;
  return RAW_ENUM_PATTERN.test(value.trim());
}

export function resolvePreflightAccountHealthLabel(
  row: CampaignPreflightAccount,
  t: TFunction,
): string {
  return (
    row.account_health_label?.trim() ||
    row.runtime_status_label?.trim() ||
    (row.runtime_status
      ? t(`runtime_status_${row.runtime_status}`, { defaultValue: row.runtime_status })
      : row.account_ready_now
        ? t("campaignAccountHealthReady")
        : t("senderStatusUnknown"))
  );
}

export function resolvePreflightExecutionLabel(
  row: CampaignPreflightAccount,
  t: TFunction,
): string {
  if (row.execution_status_label?.trim()) {
    return row.execution_status_label.trim();
  }
  if (row.execution_blocker_label?.trim()) {
    return row.execution_blocker_label.trim();
  }
  if (row.execution_ready || row.eligible_now) {
    return t("campaignExecutionReadyNow");
  }
  const fallback = row.execution_blocker_code || row.block_code || row.reason_code;
  if (fallback && !isRawBackendEnum(fallback)) return fallback;
  if (fallback === "MIN_INTERVAL_ACTIVE") {
    return t("campaignExecutionMinInterval");
  }
  if (fallback === "CAMPAIGN_NOT_PREPARED") {
    return t("campaignExecutionNotPrepared");
  }
  return t("campaignExecutionBlocked");
}

export function formatPreflightBlockers(
  preflight: CampaignPreflight,
  t: TFunction,
): string[] {
  const dedicated = new Set([
    "CAMPAIGN_CAPACITY_UNKNOWN",
    "CAMPAIGN_NO_SENDERS",
    "NO_WORKER_CONSUMER",
  ]);
  const items = preflight.blockers
    .filter((b) => b.code !== "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED")
    .filter((b) => !dedicated.has(b.code))
    .map((b) => b.message?.trim())
    .filter(Boolean) as string[];
  if (items.length > 0) return items;
  if (
    preflight.controlled_production_confirmation_required &&
    preflight.controlled_production_label?.trim()
  ) {
    return [];
  }
  if (dedicated.has(preflight.code)) return [];
  if (preflight.message?.trim()) return [preflight.message.trim()];
  return [t("campaignStartBlocked")];
}

export function isStartActionable(preflight: CampaignPreflight | null): boolean {
  if (!preflight) return false;
  if (preflight.redis_ok === false || preflight.redis_available === false) return false;
  if (preflight.capacity_known === false || preflight.capacity_confidence === "unknown") {
    return false;
  }
  if (preflight.allowed_to_start) return true;
  return Boolean(preflight.allowed_to_start_after_confirmation);
}

export function isCapacityUnknown(preflight: CampaignPreflight | null): boolean {
  if (!preflight) return true;
  if (preflight.capacity_known === false) return true;
  if (preflight.redis_ok === false || preflight.redis_available === false) return true;
  return preflight.capacity_confidence === "unknown";
}

export function formatUnknownOrCount(
  value: number | null | undefined,
  unknown: boolean,
  unknownLabel: string,
): string {
  if (unknown || value == null) return unknownLabel;
  return String(value);
}

export function formatReadinessRatio(
  numerator: number | null | undefined,
  assigned: number | null | undefined,
  unknownLabel: string,
  options?: { unknown?: boolean },
): string {
  if (options?.unknown) return unknownLabel;
  const assignedCount = assigned ?? 0;
  if (assignedCount <= 0) return unknownLabel;
  return `${numerator ?? 0}/${assignedCount}`;
}

export type PreflightOperationalAlerts = {
  redisUnavailable: boolean;
  workerNotRunning: boolean;
  noSenders: boolean;
};

export function resolvePreflightOperationalAlerts(
  preflight: CampaignPreflight | null,
): PreflightOperationalAlerts {
  if (!preflight) {
    return { redisUnavailable: false, workerNotRunning: false, noSenders: false };
  }
  const codes = new Set(
    [preflight.code, ...(preflight.blockers ?? []).map((item) => item.code)].filter(Boolean),
  );
  const assigned = preflight.assigned_accounts ?? 0;
  const redisUnavailable =
    preflight.redis_ok === false ||
    preflight.redis_available === false ||
    codes.has("CAMPAIGN_CAPACITY_UNKNOWN");
  const noSenders = assigned === 0 || codes.has("CAMPAIGN_NO_SENDERS");
  const authenticated = preflight.authenticated_accounts ?? 0;
  const workerReady =
    preflight.worker_ready_accounts ??
    preflight.accounts.filter((row) => row.worker_coverage === true).length;
  const workerCode = codes.has("NO_WORKER_CONSUMER");
  const authenticatedNoWorker = preflight.accounts.some(
    (row) =>
      row.runtime_status === "AUTHENTICATED_NO_WORKER" ||
      row.blocker_code === "AUTHENTICATED_NO_WORKER" ||
      row.execution_blocker_code === "NO_WORKER_CONSUMER",
  );
  const workerNotRunning =
    !noSenders &&
    !redisUnavailable &&
    (workerCode || authenticatedNoWorker || (authenticated > 0 && workerReady === 0));
  return { redisUnavailable, workerNotRunning, noSenders };
}

export function requiresControlledProductionConfirmation(
  preflight: CampaignPreflight | null,
): boolean {
  return Boolean(preflight?.controlled_production_confirmation_required);
}

export function hasTechnicalStartBlockers(preflight: CampaignPreflight | null): boolean {
  if (!preflight) return true;
  if (preflight.technical_ready) return false;
  return !preflight.allowed_to_start && !preflight.allowed_to_start_after_confirmation;
}

export function controlledProductionStatusLabel(
  preflight: CampaignPreflight | null,
  t: TFunction,
): string | null {
  if (!preflight?.controlled_production_confirmation_required) return null;
  return (
    preflight.controlled_production_label?.trim() ||
    t("campaignControlledConfirmationRequired")
  );
}

export type CampaignPreparationUiState =
  | "ready"
  | "incomplete"
  | "needs_retry"
  | "in_progress";

export function resolvePreparationUiState(
  preflight: CampaignPreflight | null,
  preparing = false,
): CampaignPreparationUiState {
  if (!preflight) return "in_progress";
  if (preparing) return "in_progress";
  if (preflight.campaign_prepared) return "ready";
  const blockers = preflight.preparation_blockers ?? [];
  if (blockers.length > 0) return "incomplete";
  if (preflight.preparation_ready) return "needs_retry";
  return "incomplete";
}

export function preparationStatusLabel(
  state: CampaignPreparationUiState,
  t: TFunction,
): string {
  switch (state) {
    case "ready":
      return t("campaignPreparationReady");
    case "in_progress":
      return t("campaignPreparationInProgress");
    case "needs_retry":
      return t("campaignPreparationFailed");
    default:
      return t("campaignPreparationIncomplete");
  }
}

export function preflightNeedsAutoRefresh(preflight: CampaignPreflight | null): boolean {
  if (!preflight) return false;
  if (
    !preflight.campaign_prepared &&
    preflight.preparation_ready &&
    !(preflight.preparation_blockers?.length)
  ) {
    return true;
  }
  if (preflight.accounts.some((row) => row.execution_blocker_code === "MIN_INTERVAL_ACTIVE")) {
    return true;
  }
  if (preflight.accounts.some((row) => row.next_allowed_at)) {
    return true;
  }
  return ["WAITING_CAPACITY", "WAITING_WINDOW"].includes(preflight.execution_safety_state);
}

export function nextPreflightRefreshMs(preflight: CampaignPreflight | null): number {
  if (!preflight) return 30_000;
  const times = preflight.accounts
    .map((row) => row.next_allowed_at)
    .filter(Boolean)
    .map((iso) => Date.parse(String(iso)))
    .filter((ms) => Number.isFinite(ms) && ms > Date.now());
  if (times.length === 0) return 15_000;
  const delta = Math.min(...times) - Date.now();
  return Math.max(5_000, Math.min(delta + 1_000, 60_000));
}
