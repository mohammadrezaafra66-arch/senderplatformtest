import type { TFunction } from "i18next";
import { ApiError } from "@/lib/api";

const keys: Record<string, string> = {
  TEMPLATE_MISSING: "prepareErrorTemplateMissing",
  NO_RECIPIENTS: "prepareErrorNoRecipients",
  NO_SENDERS: "prepareErrorNoSenders",
  INSUFFICIENT_ADVERTISING_PRODUCTS: "prepareErrorInsufficientProducts",
  PRODUCT_FEED_EMPTY: "prepareErrorProductFeedEmpty",
  PRODUCT_FEED_STALE: "prepareErrorProductFeedStale",
  PRODUCT_FEED_UNAVAILABLE: "prepareErrorProductFeedUnavailable",
  GPT_UNAVAILABLE: "prepareErrorGptUnavailable",
  GPT_RENDER_FAILED: "prepareErrorGptFailed",
  CAMPAIGN_STATUS_NOT_PREPAREABLE: "prepareErrorStatusNotPrepareable",
};

export function campaignPreparationError(err: unknown, t: TFunction) {
  if (!(err instanceof ApiError)) return t("actionFailed");
  const key = err.code ? keys[err.code] : undefined;
  return key ? t(key) : err.message;
}
