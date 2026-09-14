import type { TFunction } from "i18next";

export const PLATFORM_OPTIONS = ["whatsapp", "telegram", "bale", "rubika"] as const;

export const ACCOUNT_STATUS_OPTIONS = [
  "active",
  "resting",
  "banned",
  "requires_login",
] as const;

/** Lifecycle Account.status — NOT connection. */
export function accountStatusLabel(status: string, t: (key: string) => string): string {
  const key = `account_status_${status}`;
  const translated = t(key);
  return translated === key ? status : translated;
}

export function accountStatusColor(status: string): string {
  if (status === "active") return "#166534";
  if (status === "resting") return "#b45309";
  if (status === "banned") return "#991b1b";
  if (status === "requires_login") return "#1d4ed8";
  return "inherit";
}

/** L18 normalized runtime statuses from backend. */
export const RUNTIME_STATUS_VALUES = [
  "DISABLED",
  "LOGIN_REQUIRED",
  "OTP_WAITING",
  "AUTHENTICATING",
  "AUTHENTICATED_NO_WORKER",
  "READY",
  "MANUAL_REVIEW",
  "SESSION_ERROR",
  "CONNECTION_ERROR",
  "CONFIG_ERROR",
  "NOT_APPLICABLE",
] as const;

export type RuntimeStatusValue = (typeof RUNTIME_STATUS_VALUES)[number];

const RUNTIME_FALLBACK_FA: Record<string, string> = {
  DISABLED: "غیرفعال",
  LOGIN_REQUIRED: "نیاز به ورود",
  OTP_WAITING: "در انتظار کد",
  AUTHENTICATING: "در حال احراز",
  AUTHENTICATED_NO_WORKER: "احراز شده، Worker آماده نیست",
  READY: "آماده ارسال",
  MANUAL_REVIEW: "نیازمند بررسی",
  SESSION_ERROR: "خطای سشن",
  CONNECTION_ERROR: "خطای اتصال",
  CONFIG_ERROR: "خطای پیکربندی",
  NOT_APPLICABLE: "نامرتبط",
};

const REASON_LABEL_FA: Record<string, string> = {
  LOGIN_REQUIRED: "نیاز به ورود",
  ACCOUNT_REQUIRES_LOGIN: "نیاز به ورود",
  SESSION_INVALIDATED: "نیاز به ورود مجدد",
  NO_WORKER_COVERAGE: "احراز شده، Worker آماده نیست",
  LEGACY_NO_WORKER: "احراز شده، Worker آماده نیست",
  WORKER_COVERED_NOT_DISPATCH: "Worker آماده",
  READY: "آماده ارسال",
  WORKER_STALE: "Worker قطع شده",
  ACCOUNT_RESTING: "متوقف",
  ACCOUNT_BANNED: "مسدود",
  QUARANTINED: "قرنطینه",
};

export function runtimeStatusLabel(
  status: string | null | undefined,
  t?: TFunction | ((key: string) => string),
  backendLabel?: string | null,
  reasonCode?: string | null,
): string {
  if (backendLabel && backendLabel.trim()) return backendLabel;
  if (reasonCode && REASON_LABEL_FA[reasonCode]) return REASON_LABEL_FA[reasonCode];
  if (!status) return "—";
  if (t) {
    const key = `runtime_status_${status}`;
    const translated = t(key);
    if (translated !== key) return translated;
  }
  return RUNTIME_FALLBACK_FA[status] ?? status;
}

export function runtimeStatusIcon(status: string | null | undefined): string {
  switch (status) {
    case "READY":
      return "🟢";
    case "AUTHENTICATED_NO_WORKER":
      return "🟢";
    case "LOGIN_REQUIRED":
    case "OTP_WAITING":
    case "AUTHENTICATING":
      return "🟡";
    case "MANUAL_REVIEW":
      return "🟠";
    case "SESSION_ERROR":
    case "CONNECTION_ERROR":
    case "CONFIG_ERROR":
      return "🔴";
    case "DISABLED":
    case "NOT_APPLICABLE":
      return "⚫";
    default:
      return "⚪";
  }
}

export function runtimeStatusColor(status: string | null | undefined): string {
  switch (status) {
    case "READY":
      return "#166534";
    case "AUTHENTICATED_NO_WORKER":
      return "#15803d";
    case "LOGIN_REQUIRED":
    case "OTP_WAITING":
    case "AUTHENTICATING":
      return "#a16207";
    case "MANUAL_REVIEW":
      return "#c2410c";
    case "SESSION_ERROR":
    case "CONNECTION_ERROR":
    case "CONFIG_ERROR":
      return "#b91c1c";
    case "DISABLED":
    case "NOT_APPLICABLE":
      return "#525252";
    default:
      return "#374151";
  }
}

/** Never map enabled/active lifecycle directly to "connected". */
export function isLifecycleConnectedLie(status: string, runtime?: string | null): boolean {
  return status === "active" && !runtime;
}
