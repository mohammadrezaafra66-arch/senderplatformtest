import { describe, expect, it } from "vitest";

import {
  controlledProductionStatusLabel,
  formatPreflightBlockers,
  formatReadinessRatio,
  formatUnknownOrCount,
  isRawBackendEnum,
  isStartActionable,
  preparationStatusLabel,
  requiresControlledProductionConfirmation,
  resolvePreparationUiState,
  resolvePreflightAccountHealthLabel,
  resolvePreflightExecutionLabel,
  resolvePreflightOperationalAlerts,
} from "./campaign-preflight-display";
import { formatAccountCapRemaining } from "./account-message-limits";

const t = (key: string, options?: { defaultValue?: string }) => {
  const map: Record<string, string> = {
    campaignAccountHealthReady: "اکانت سالم",
    campaignExecutionReadyNow: "آماده ارسال همین لحظه",
    campaignExecutionMinInterval: "در انتظار فاصله مجاز ارسال",
    campaignExecutionNotPrepared: "کمپین هنوز آماده‌سازی نشده است.",
    campaignExecutionBlocked: "ارسال فوری ممکن نیست",
    campaignPreparationReady: "آماده ارسال",
    campaignPreparationIncomplete: "اطلاعات کمپین کامل نیست",
    campaignPreparationFailed: "آماده‌سازی انجام نشد",
    campaignControlledConfirmationRequired: "تأیید نهایی لازم است",
    campaignStartBlocked: "شروع مسدود است",
    runtime_status_READY: "آماده ارسال",
    runtime_status_LOGIN_REQUIRED: "نیاز به ورود",
    runtime_status_MANUAL_REVIEW: "نیازمند بررسی",
    runtime_status_SESSION_ERROR: "خطای سشن",
  };
  return map[key] ?? options?.defaultValue ?? key;
};

describe("campaign preflight display semantics", () => {
  it("detects raw backend enums", () => {
    expect(isRawBackendEnum("MIN_INTERVAL_ACTIVE")).toBe(true);
    expect(isRawBackendEnum("آماده ارسال")).toBe(false);
  });

  it("CASE 1: READY + execution ready shows immediate send label", () => {
    const label = resolvePreflightExecutionLabel(
      {
        execution_ready: true,
        eligible_now: true,
        execution_status_label: "آماده ارسال",
      } as never,
      t,
    );
    expect(label).toBe("آماده ارسال");
  });

  it("CASE 2: MIN_INTERVAL_ACTIVE shows Persian wait label", () => {
    const label = resolvePreflightExecutionLabel(
      {
        execution_ready: false,
        eligible_now: false,
        execution_blocker_code: "MIN_INTERVAL_ACTIVE",
        execution_blocker_label: "در انتظار فاصله مجاز ارسال",
      } as never,
      t,
    );
    expect(label).toBe("در انتظار فاصله مجاز ارسال");
    expect(label).not.toContain("MIN_INTERVAL_ACTIVE");
  });

  it("CASE 6: campaign not prepared shows preparation blocker", () => {
    const label = resolvePreflightExecutionLabel(
      {
        execution_ready: false,
        execution_blocker_code: "CAMPAIGN_NOT_PREPARED",
        execution_blocker_label: "کمپین هنوز آماده‌سازی نشده است.",
      } as never,
      t,
    );
    expect(label).toBe("کمپین هنوز آماده‌سازی نشده است.");
  });

  it("separates account health from execution status", () => {
    const health = resolvePreflightAccountHealthLabel(
      {
        account_health_label: "آماده ارسال",
        runtime_status: "READY",
      } as never,
      t,
    );
    const execution = resolvePreflightExecutionLabel(
      {
        account_ready_now: true,
        execution_blocker_label: "در انتظار فاصله مجاز ارسال",
      } as never,
      t,
    );
    expect(health).toBe("آماده ارسال");
    expect(execution).toBe("در انتظار فاصله مجاز ارسال");
  });

  it("maps preparation UI states from preflight", () => {
    expect(
      resolvePreparationUiState({ campaign_prepared: true } as never),
    ).toBe("ready");
    expect(
      resolvePreparationUiState({
        campaign_prepared: false,
        preparation_blockers: [{ code: "NO_RECIPIENTS", message: "x" }],
      } as never),
    ).toBe("incomplete");
    expect(
      resolvePreparationUiState({
        campaign_prepared: false,
        preparation_ready: true,
        preparation_blockers: [],
      } as never),
    ).toBe("needs_retry");
    expect(preparationStatusLabel("ready", t)).toBe("آماده ارسال");
  });

  it("distinguishes controlled confirmation from technical blockers", () => {
    const preflight = {
      allowed_to_start: false,
      allowed_to_start_after_confirmation: true,
      technical_ready: true,
      controlled_production_confirmation_required: true,
      controlled_production_label: "تأیید نهایی لازم است",
      blockers: [
        {
          code: "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED",
          message: "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است.",
        },
      ],
    } as never;
    expect(isStartActionable(preflight)).toBe(true);
    expect(requiresControlledProductionConfirmation(preflight)).toBe(true);
    expect(formatPreflightBlockers(preflight, t)).toEqual([]);
    expect(controlledProductionStatusLabel(preflight, t)).toBe("تأیید نهایی لازم است");
  });

  it("does not render 0/0 for unknown capacity or missing senders", () => {
    expect(formatReadinessRatio(0, 0, "نامشخص")).toBe("نامشخص");
    expect(formatReadinessRatio(0, 2, "نامشخص", { unknown: true })).toBe("نامشخص");
    expect(formatReadinessRatio(0, 2, "نامشخص")).toBe("0/2");
    expect(formatUnknownOrCount(0, true, "نامشخص")).toBe("نامشخص");
    expect(formatUnknownOrCount(0, false, "نامشخص")).toBe("0");
  });

  it("does not treat unlimited remaining as redis-unknown", () => {
    expect(
      formatAccountCapRemaining({
        unlimited: true,
        remaining: null,
        quotaKnown: false,
        unlimitedLabel: "بدون محدودیت",
        unknownLabel: "ظرفیت لحظه‌ای نامشخص",
      }),
    ).toBe("بدون محدودیت");
    expect(
      formatAccountCapRemaining({
        unlimited: false,
        remaining: null,
        quotaKnown: false,
        unknownLabel: "ظرفیت لحظه‌ای نامشخص",
      }),
    ).toBe("ظرفیت لحظه‌ای نامشخص");
  });

  it("splits redis / worker / no-sender alerts", () => {
    expect(
      resolvePreflightOperationalAlerts({
        redis_ok: false,
        assigned_accounts: 0,
        code: "CAMPAIGN_NO_SENDERS",
        blockers: [
          { code: "CAMPAIGN_NO_SENDERS", message: "no" },
          { code: "CAMPAIGN_CAPACITY_UNKNOWN", message: "redis" },
        ],
        accounts: [],
      } as never),
    ).toEqual({ redisUnavailable: true, workerNotRunning: false, noSenders: true });

    expect(
      resolvePreflightOperationalAlerts({
        redis_ok: true,
        assigned_accounts: 2,
        authenticated_accounts: 2,
        worker_ready_accounts: 0,
        campaign_eligible_accounts: 0,
        code: "NO_WORKER_CONSUMER",
        blockers: [{ code: "NO_WORKER_CONSUMER", message: "worker" }],
        accounts: [{ runtime_status: "AUTHENTICATED_NO_WORKER", worker_coverage: false }],
      } as never),
    ).toEqual({ redisUnavailable: false, workerNotRunning: true, noSenders: false });
  });

  it("blocks start when redis capacity is unknown or worker is off", () => {
    expect(
      isStartActionable({
        allowed_to_start: true,
        redis_ok: false,
        capacity_known: false,
      } as never),
    ).toBe(false);
    expect(
      isStartActionable({
        allowed_to_start: false,
        redis_ok: true,
        capacity_known: true,
        allowed_to_start_after_confirmation: false,
      } as never),
    ).toBe(false);
  });
});
