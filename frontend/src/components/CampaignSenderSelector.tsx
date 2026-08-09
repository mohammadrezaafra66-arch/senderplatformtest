import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState } from "@/components/ui";
import type { AccountItem } from "@/types/account";
import type { PlatformOption } from "@/types/campaign";
import { accountDisplayName, compatibleActiveAccounts, toggleOrderedAccount } from "@/utils/sender-accounts";

export type SenderMode = "auto" | "manual";

export function CampaignSenderSelector({
  platform,
  accounts,
  mode,
  selectedIds,
  loading,
  loadError,
  disabled,
  onModeChange,
  onSelectedIdsChange,
  onRetry,
}: {
  platform: PlatformOption;
  accounts: AccountItem[];
  mode: SenderMode;
  selectedIds: number[];
  loading: boolean;
  loadError: string | null;
  disabled?: boolean;
  onModeChange: (mode: SenderMode) => void;
  onSelectedIdsChange: (ids: number[]) => void;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  const compatible = useMemo(
    () => compatibleActiveAccounts(accounts, platform),
    [accounts, platform],
  );

  return (
    <fieldset className="mmp-sender-selector" disabled={disabled}>
      <legend>{t("senderNumbers")}</legend>
      <div className="mmp-stack">
        <label className="mmp-stack">
          <input type="radio" checked={mode === "auto"} onChange={() => onModeChange("auto")} />
          <span>{t("senderModeAuto")}</span>
        </label>
        <label className="mmp-stack">
          <input type="radio" checked={mode === "manual"} onChange={() => onModeChange("manual")} />
          <span>{t("senderModeManual")}</span>
        </label>
      </div>

      {mode === "auto" ? (
        <>
          <p className="mmp-muted">{t("senderAutoHint")}</p>
          {!loading && !loadError && compatible.length === 0 ? <Alert>{t("senderAutoEmptyWarning")}</Alert> : null}
        </>
      ) : loading ? (
        <EmptyState>{t("senderAccountsLoading")}</EmptyState>
      ) : loadError ? (
        <Alert>
          {loadError} <Button size="sm" onClick={onRetry}>{t("retry")}</Button>
        </Alert>
      ) : compatible.length === 0 ? (
        <Alert>{t("senderManualEmpty")}</Alert>
      ) : (
        <div className="mmp-sender-list">
          {compatible.map((account) => {
            const order = selectedIds.indexOf(account.id);
            return (
              <label className="mmp-sender-row" key={account.id}>
                <input
                  type="checkbox"
                  checked={order >= 0}
                  onChange={(event) =>
                    onSelectedIdsChange(toggleOrderedAccount(selectedIds, account.id, event.target.checked))
                  }
                />
                <span className="mmp-sender-order">{order >= 0 ? order + 1 : "—"}</span>
                <span>
                  <strong>{accountDisplayName(account)}</strong>
                  {account.label && account.account_identifier ? <small>{account.account_identifier}</small> : null}
                  <small>{account.platform} · {t(`account_status_${account.status}`)}</small>
                </span>
              </label>
            );
          })}
        </div>
      )}
      {mode === "manual" && !loading && !loadError && selectedIds.length === 0 ? (
        <div className="mmp-field-error">{t("senderManualRequired")}</div>
      ) : null}
    </fieldset>
  );
}
