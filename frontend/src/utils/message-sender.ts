import type { CampaignRecipientItem } from "@/types/campaign";

export type MessageSenderPresentation = {
  kind: "sender" | "account-id" | "unknown" | "unassigned";
  primary: string;
  secondary: string | null;
};

export function messageSenderPresentation(
  item: Pick<CampaignRecipientItem, "final_message_id" | "account_id" | "sender_account">,
  labels: { account: (id: number) => string; unknown: string; unassigned: string },
): MessageSenderPresentation {
  const sender = item.sender_account;
  if (sender) {
    const label = sender.label?.trim();
    const identifier = sender.account_identifier?.trim();
    return {
      kind: "sender",
      primary: label || identifier || labels.account(sender.account_id),
      secondary: [label ? identifier : null, sender.platform].filter(Boolean).join(" · ") || null,
    };
  }
  if (item.account_id != null) {
    return { kind: "account-id", primary: labels.account(item.account_id), secondary: null };
  }
  if (item.final_message_id === null) {
    return { kind: "unassigned", primary: labels.unassigned, secondary: null };
  }
  return { kind: "unknown", primary: labels.unknown, secondary: null };
}
