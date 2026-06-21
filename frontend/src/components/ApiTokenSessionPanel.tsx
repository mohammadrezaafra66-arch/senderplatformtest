import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import {
  fetchAccountSessionStatus,
  registerAccountSession,
} from "@/lib/accounts-api";
import { OperationalSendTestForm } from "@/components/OperationalSendTestForm";
import type { AccountSessionStatus } from "@/types/account";

const panelInnerStyle: React.CSSProperties = {
  marginTop: 8,
  padding: 12,
  borderRadius: 10,
  border: "1px solid rgba(0,0,0,0.1)",
  background: "rgba(0,0,0,0.02)",
  display: "grid",
  gap: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(0,0,0,0.2)",
  width: "100%",
  fontFamily: "monospace",
  fontSize: 13,
  direction: "ltr",
  textAlign: "left",
};

const badgeStyle = (color: string): React.CSSProperties => ({
  display: "inline-block",
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: color,
  color: "#fff",
});

type ApiTokenSessionPanelProps = {
  accountId: number;
  platform: "bale" | "telegram" | "rubika";
  accountIdentifier?: string | null;
  onRegistered?: () => void;
};

export function ApiTokenSessionPanel({
  accountId,
  platform,
  accountIdentifier,
  onRegistered,
}: ApiTokenSessionPanelProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<AccountSessionStatus | null>(null);
  const [token, setToken] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAccountSessionStatus(accountId);
      setStatus(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("sessionLoadError"));
    } finally {
      setLoading(false);
    }
  }, [accountId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    if (!token.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await registerAccountSession(accountId, {
        session_payload: token.trim(),
      });
      setNotice(result.message);
      setToken("");
      await load();
      onRegistered?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSaving(false);
    }
  }

  if (loading && !status) {
    return <div style={panelInnerStyle}>{t("loading")}</div>;
  }

  const ready = status?.ready_for_delivery ?? false;

  return (
    <div style={panelInnerStyle}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>{t("sessionTitle", { platform })}</strong>
        <span style={badgeStyle(ready ? "#166534" : "#b45309")}>
          {ready ? t("sessionReady") : t("sessionNotReady")}
        </span>
      </div>

      {status ? (
        <div style={{ fontSize: 13, lineHeight: 1.7, opacity: 0.9 }}>
          <div>{status.message}</div>
          <div>
            {t("sessionRegistered")}: {status.session_registered ? t("yes") : t("no")}
          </div>
          <div>
            {t("sessionType")}: {status.session_type}
          </div>
        </div>
      ) : null}

      <form onSubmit={(e) => void handleRegister(e)} style={{ display: "grid", gap: 8 }}>
        <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
          <span>{t("sessionPayloadLabel")}</span>
          <textarea
            value={token}
            onChange={(e) => setToken(e.target.value)}
            rows={3}
            placeholder={t("sessionPayloadPlaceholder")}
            style={inputStyle}
          />
        </label>
        <div style={{ fontSize: 12, opacity: 0.75 }}>{t("sessionPayloadHint")}</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="submit" disabled={saving} style={{ padding: "8px 12px", borderRadius: 8 }}>
            {saving ? t("loading") : t("sessionSave")}
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            style={{ padding: "8px 12px", borderRadius: 8 }}
          >
            {loading ? t("loading") : t("refresh")}
          </button>
        </div>
      </form>

      {error ? (
        <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
          {error}
        </div>
      ) : null}
      {notice ? <div style={{ color: "#166534", fontSize: 13 }}>{notice}</div> : null}

      <OperationalSendTestForm
        accountId={accountId}
        defaultRecipient={accountIdentifier}
        recipientLabel={t("opsSendRecipientChat")}
        recipientPlaceholder={t("opsSendRecipientChatPlaceholder")}
        sessionReady={ready}
      />
    </div>
  );
}
