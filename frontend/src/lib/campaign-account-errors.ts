import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";

const keys: Record<string, string> = {
  account_not_found: "senderErrorNotFound",
  account_inactive: "senderErrorInactive",
  account_platform_mismatch: "senderErrorPlatformMismatch",
  no_active_sender_account: "senderErrorNoActive",
  no_enabled_campaign_sender: "senderErrorNoEnabled",
};

export function campaignAccountError(err: unknown, t: TFunction) {
  if (!(err instanceof ApiError)) return t("actionFailed");
  const key = err.code ? keys[err.code] : undefined;
  return key ? t(key) : err.message;
}
