/** Map structured session readiness codes/errors to UI labels. */

export type SessionReadinessDisplay = {
  labelKey: string;
  color: string;
};

const CODE_MAP: Record<string, SessionReadinessDisplay> = {
  READY: { labelKey: "sessionReady", color: "#166534" },
  SESSION_MISSING: { labelKey: "sessionReadinessMissing", color: "#b45309" },
  session_missing: { labelKey: "sessionReadinessMissing", color: "#b45309" },
  SESSION_INVALID: { labelKey: "sessionReadinessInvalid", color: "#991b1b" },
  session_invalid: { labelKey: "sessionReadinessInvalid", color: "#991b1b" },
  SESSION_DECRYPT_FAILED: { labelKey: "sessionReadinessInvalid", color: "#991b1b" },
  session_decrypt_failed: { labelKey: "sessionReadinessInvalid", color: "#991b1b" },
  ACCOUNT_REQUIRES_LOGIN: { labelKey: "sessionReadinessLoginRequired", color: "#1d4ed8" },
  account_requires_login: { labelKey: "sessionReadinessLoginRequired", color: "#1d4ed8" },
  ACCOUNT_DISABLED: { labelKey: "sessionReadinessDisabled", color: "#b45309" },
  account_disabled: { labelKey: "sessionReadinessDisabled", color: "#b45309" },
  ACCOUNT_BANNED: { labelKey: "sessionReadinessDisabled", color: "#991b1b" },
  account_banned: { labelKey: "sessionReadinessDisabled", color: "#991b1b" },
  USER_ACCOUNT_DISABLED: { labelKey: "sessionReadinessConfigDisabled", color: "#7c2d12" },
  user_account_disabled: { labelKey: "sessionReadinessConfigDisabled", color: "#7c2d12" },
  CONFIG_INVALID: { labelKey: "sessionReadinessConfigError", color: "#7c2d12" },
  config_invalid: { labelKey: "sessionReadinessConfigError", color: "#7c2d12" },
  DELIVERY_MODE_DISABLED: { labelKey: "sessionReadinessConfigDisabled", color: "#7c2d12" },
  delivery_mode_disabled: { labelKey: "sessionReadinessConfigDisabled", color: "#7c2d12" },
};

export function sessionReadinessDisplay(
  ready: boolean,
  code?: string | null,
  error?: string | null,
): SessionReadinessDisplay {
  if (ready) {
    return CODE_MAP.READY;
  }
  const key = (code || error || "").trim();
  if (key && CODE_MAP[key]) {
    return CODE_MAP[key];
  }
  return { labelKey: "sessionNotReady", color: "#b45309" };
}
