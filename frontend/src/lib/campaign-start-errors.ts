import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";

const START_ERROR_KEYS: Record<string, string> = {
  CONTROLLED_PRODUCTION_APPROVAL_REQUIRED: "campaignControlledConfirmationRequired",
  CAMPAIGN_NOT_PREPARED: "campaignExecutionNotPrepared",
  NO_READY_SENDER: "campaignExecutionBlocked",
  CAMPAIGN_SENDER_BLOCKED: "campaignExecutionBlocked",
  CAMPAIGN_NO_SENDERS: "prepareErrorNoSenders",
  CAMPAIGN_NO_MESSAGES: "campaignStartBlocked",
};

export function campaignStartError(err: unknown, t: TFunction): string {
  if (!(err instanceof ApiError)) return t("actionFailed");
  const key = err.code ? START_ERROR_KEYS[err.code] : undefined;
  if (key) return t(key);
  if (err.message && !/^[A-Z][A-Z0-9_]+$/.test(err.message.trim())) {
    return err.message;
  }
  return t("actionFailed");
}
