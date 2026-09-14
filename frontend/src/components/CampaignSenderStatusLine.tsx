import type { TFunction } from "i18next";

import { runtimeStatusColor } from "@/utils/account-status";
import {
  resolveCampaignSenderBlockerLabel,
  resolveCampaignSenderStatusLabel,
} from "@/utils/sender-accounts";

/** Single shared operational status line for all campaign sender UIs. */
export function CampaignSenderStatusLine({
  account,
  t,
  showEnabledHint = false,
}: {
  account: {
    campaign_status_label?: string | null;
    runtime_status_label?: string | null;
    runtime_status?: string | null;
    status?: string | null;
    campaign_eligible?: boolean | null;
    campaign_blocker_label?: string | null;
    campaign_blocker_code?: string | null;
    blocker_label?: string | null;
    blocker_code?: string | null;
    account_enabled?: boolean | null;
  };
  t: TFunction | ((key: string, options?: { defaultValue?: string }) => string);
  showEnabledHint?: boolean;
}) {
  const statusLabel = resolveCampaignSenderStatusLabel(account, t);
  const blocker = resolveCampaignSenderBlockerLabel(account, t);
  const executionBlocker =
    (account as { execution_blocker_label?: string | null }).execution_blocker_label?.trim() ||
    null;
  const enabledHint =
    showEnabledHint && account.status === "active"
      ? t("accountEnabledOn")
      : null;

  return (
    <>
      <small
        style={{
          display: "block",
          color: runtimeStatusColor(account.runtime_status),
        }}
      >
        {statusLabel}
        {executionBlocker && executionBlocker !== statusLabel
          ? ` — ${executionBlocker}`
          : blocker && blocker !== statusLabel
            ? ` — ${blocker}`
            : ""}
      </small>
      {enabledHint ? (
        <small className="mmp-muted" style={{ display: "block" }}>
          {t("accountLifecycleStatus")}: {enabledHint}
        </small>
      ) : null}
    </>
  );
}
