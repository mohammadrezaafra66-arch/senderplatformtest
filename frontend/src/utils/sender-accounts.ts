import type { AccountItem } from "@/types/account";
import type { CampaignSenderAccount, PlatformOption } from "@/types/campaign";

export function compatibleActiveAccounts(accounts: AccountItem[], platform: PlatformOption) {
  return accounts.filter((account) => account.status === "active" && account.platform === platform);
}

export function toggleOrderedAccount(ids: number[], id: number, checked: boolean) {
  const withoutId = ids.filter((item) => item !== id);
  return checked ? [...withoutId, id] : withoutId;
}

export function accountDisplayName(account: Pick<AccountItem, "label" | "account_identifier">) {
  return account.label?.trim() || account.account_identifier?.trim() || "—";
}

export function orderedSenders(campaign: {
  account_ids?: number[];
  sender_accounts?: CampaignSenderAccount[];
}) {
  const senders = [...(campaign.sender_accounts ?? [])];
  return senders.sort((a, b) => a.priority - b.priority);
}
