import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Alert, Button, EmptyState } from "@/components/ui";
import { CampaignSenderStatusLine } from "@/components/CampaignSenderStatusLine";
import type { AccountItem } from "@/types/account";
import type { PlatformOption } from "@/types/campaign";
import {
  compatiblePlatformAccounts,
  filterSenderAccounts,
  isCampaignEligible,
  resolveDisplayIdentity,
  toggleOrderedAccount,
  type SenderFilter,
} from "@/utils/sender-accounts";

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
  const [filter, setFilter] = useState<SenderFilter>("all");
  const compatible = useMemo(
    () => compatiblePlatformAccounts(accounts, platform),
    [accounts, platform],
  );
  const visible = useMemo(
    () => filterSenderAccounts(compatible, filter),
    [compatible, filter],
  );
  const readyCount = useMemo(
    () => compatible.filter(isCampaignEligible).length,
    [compatible],
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
          {!loading && !loadError && readyCount === 0 ? <Alert>{t("senderAutoEmptyWarning")}</Alert> : null}
          {!loading && !loadError && readyCount > 0 ? (
            <p className="mmp-muted">{t("senderAutoReadyCount", { count: readyCount })}</p>
          ) : null}
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
        <>
          <div className="mmp-sender-filters" style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {(
              [
                ["all", "senderFilterAll"],
                ["ready", "senderFilterReady"],
                ["needs_action", "senderFilterNeedsAction"],
              ] as const
            ).map(([key, labelKey]) => (
              <Button
                key={key}
                size="sm"
                variant={filter === key ? "primary" : "default"}
                onClick={() => setFilter(key)}
                type="button"
              >
                {t(labelKey)}
              </Button>
            ))}
          </div>
          <div className="mmp-sender-list">
            {visible.map((account) => {
              const order = selectedIds.indexOf(account.id);
              const eligible = isCampaignEligible(account);
              const selected = order >= 0;
              const checkboxDisabled = !eligible && !selected;
              return (
                <label
                  className="mmp-sender-row"
                  key={account.id}
                  style={{ opacity: checkboxDisabled ? 0.72 : 1 }}
                >
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={checkboxDisabled}
                    onChange={(event) =>
                      onSelectedIdsChange(
                        toggleOrderedAccount(selectedIds, account.id, event.target.checked),
                      )
                    }
                  />
                  <span className="mmp-sender-order">{order >= 0 ? order + 1 : "—"}</span>
                  <span>
                    <strong>{resolveDisplayIdentity(account)}</strong>
                    <small style={{ display: "block" }}>{account.platform}</small>
                    <CampaignSenderStatusLine account={account} t={t} />
                    {!eligible ? (
                      <small className="mmp-muted" style={{ display: "block" }}>
                        {t("senderAssignedNotReadyHint")}
                      </small>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>
        </>
      )}
      {mode === "manual" && !loading && !loadError && selectedIds.length === 0 ? (
        <div className="mmp-field-error">{t("senderManualRequired")}</div>
      ) : null}
    </fieldset>
  );
}
