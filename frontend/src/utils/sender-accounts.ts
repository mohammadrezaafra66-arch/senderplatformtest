import type { TFunction } from "i18next";

import type { AccountItem } from "@/types/account";
import type { CampaignSenderAccount, PlatformOption } from "@/types/campaign";
import { runtimeStatusLabel } from "@/utils/account-status";

/** Shared campaign sender operational status — never lifecycle "active"/فعال alone. */
export function resolveCampaignSenderStatusLabel(
  account: {
    campaign_status_label?: string | null;
    runtime_status_label?: string | null;
    runtime_status?: string | null;
    status?: string | null;
  },
  t: TFunction | ((key: string, options?: { defaultValue?: string }) => string),
): string {
  const backend =
    account.campaign_status_label?.trim() ||
    account.runtime_status_label?.trim() ||
    null;
  const fromRuntime = runtimeStatusLabel(account.runtime_status, t, backend);
  if (fromRuntime !== "—") return fromRuntime;
  return t("senderStatusUnknown");
}

export function resolveCampaignSenderBlockerLabel(
  account: {
    campaign_eligible?: boolean | null;
    campaign_blocker_label?: string | null;
    campaign_blocker_code?: string | null;
    blocker_label?: string | null;
    blocker_code?: string | null;
  },
  t: TFunction | ((key: string) => string),
): string | null {
  if (account.campaign_eligible !== false) return null;
  return (
    account.campaign_blocker_label?.trim() ||
    account.blocker_label?.trim() ||
    account.campaign_blocker_code?.trim() ||
    account.blocker_code?.trim() ||
    t("senderAssignedNotReadyHint")
  );
}

/** Accounts visible in the campaign sender picker for a platform (lifecycle-active). */
export function compatiblePlatformAccounts(accounts: AccountItem[], platform: PlatformOption) {
  return accounts.filter((account) => account.platform === platform && account.status === "active");
}

/** @deprecated use compatiblePlatformAccounts — name kept for older imports */
export function compatibleActiveAccounts(accounts: AccountItem[], platform: PlatformOption) {
  return compatiblePlatformAccounts(accounts, platform);
}

/** Base eligibility predicate — must match backend evaluate_campaign_sender_eligibility. */
export function isCampaignEligible(account: AccountItem): boolean {
  if (account.campaign_eligible === true) return true;
  if (account.campaign_eligible === false) return false;
  // Fallback when older API payloads lack campaign_eligible: L18 READY only.
  return account.runtime_status === "READY" && account.account_enabled !== false;
}

export function toggleOrderedAccount(ids: number[], id: number, checked: boolean) {
  const withoutId = ids.filter((item) => item !== id);
  return checked ? [...withoutId, id] : withoutId;
}

function looksLikeNonIdentity(value: string): boolean {
  const v = value.trim();
  if (!v) return true;
  if (["true", "false", "none", "null"].includes(v.toLowerCase())) return true;
  const compact = v.replace(/\s+/g, "");
  if (/^\d{1,3}$/.test(compact)) return true;
  if (/^\d+\/\d+$/.test(compact)) return true;
  return false;
}

/** Never render index/count/boolean as sender identity. */
export function resolveDisplayIdentity(account: {
  id?: number;
  account_id?: number;
  account_identifier?: string | null;
  label?: string | null;
  display_identity?: string | null;
}): string {
  if (account.display_identity?.trim()) return account.display_identity.trim();
  const phone = account.account_identifier?.trim() || "";
  if (phone) return phone;
  const label = account.label?.trim() || "";
  if (label && !looksLikeNonIdentity(label)) return label;
  const id = account.id ?? account.account_id;
  return id != null ? `اکانت #${id}` : "—";
}

export function accountDisplayName(
  account: {
    label?: string | null;
    account_identifier?: string | null;
    id?: number;
    account_id?: number;
    display_identity?: string | null;
  },
) {
  return resolveDisplayIdentity(account);
}

export function orderedSenders(campaign: {
  account_ids?: number[];
  sender_accounts?: CampaignSenderAccount[];
}) {
  const senders = [...(campaign.sender_accounts ?? [])];
  return senders.sort((a, b) => a.priority - b.priority);
}

export type SenderFilter = "all" | "ready" | "needs_action";

export function filterSenderAccounts(
  accounts: AccountItem[],
  filter: SenderFilter,
): AccountItem[] {
  if (filter === "ready") return accounts.filter(isCampaignEligible);
  if (filter === "needs_action") return accounts.filter((a) => !isCampaignEligible(a));
  return accounts;
}
