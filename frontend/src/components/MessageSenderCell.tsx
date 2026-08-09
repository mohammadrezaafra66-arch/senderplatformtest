import { useTranslation } from "react-i18next";

import type { CampaignRecipientItem } from "@/types/campaign";
import { messageSenderPresentation } from "@/utils/message-sender";

export function MessageSenderCell({ item }: { item: CampaignRecipientItem }) {
  const { t } = useTranslation();
  const sender = messageSenderPresentation(item, {
    account: (id) => t("senderAccountNumber", { id }),
    unknown: t("senderAccountUnknown"),
    unassigned: t("senderNotAssigned"),
  });

  return (
    <div className="mmp-message-sender">
      <span>{sender.primary}</span>
      {sender.secondary ? <small>{sender.secondary}</small> : null}
    </div>
  );
}
