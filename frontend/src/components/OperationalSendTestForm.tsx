import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import {
  fetchLiveSendPreflight,
  fetchOperationalSendCapabilities,
  sendAccountTestMessage,
} from "@/lib/accounts-api";
import type { LiveSendPreflight, OperationalSendCapabilities } from "@/types/account";

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(0,0,0,0.2)",
  width: "100%",
  fontSize: 13,
};

type OperationalSendTestFormProps = {
  accountId: number;
  defaultRecipient?: string | null;
  recipientLabel: string;
  recipientPlaceholder: string;
  sessionReady: boolean;
};

export function OperationalSendTestForm({
  accountId,
  defaultRecipient,
  recipientLabel,
  recipientPlaceholder,
  sessionReady,
}: OperationalSendTestFormProps) {
  const { t } = useTranslation();
  const [recipient, setRecipient] = useState(defaultRecipient ?? "");
  const [messageText, setMessageText] = useState(t("opsSendDefaultMessage"));
  const [sending, setSending] = useState(false);
  const [liveConfirm, setLiveConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [caps, setCaps] = useState<OperationalSendCapabilities | null>(null);
  const [preflight, setPreflight] = useState<LiveSendPreflight | null>(null);

  const loadPreflight = useCallback(async () => {
    try {
      const [capabilities, preflightData] = await Promise.all([
        fetchOperationalSendCapabilities(),
        fetchLiveSendPreflight(accountId),
      ]);
      setCaps(capabilities);
      setPreflight(preflightData);
    } catch {
      setCaps(null);
      setPreflight(null);
    }
  }, [accountId]);

  useEffect(() => {
    if (sessionReady) {
      void loadPreflight();
    }
  }, [sessionReady, loadPreflight]);

  async function handleSend(dryRun: boolean) {
    if (!recipient.trim()) {
      setError(t("requiredFields"));
      return;
    }
    if (!dryRun && !liveConfirm) {
      setError(t("opsSendLiveConfirmRequired"));
      return;
    }
    setSending(true);
    setError(null);
    setResult(null);
    try {
      const response = await sendAccountTestMessage(accountId, {
        message_text: messageText.trim(),
        recipient: recipient.trim(),
        dry_run: dryRun,
        confirm_live_send: !dryRun,
      });
      setResult(response.message);
      if (!dryRun) {
        await loadPreflight();
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSending(false);
    }
  }

  const liveAllowed = Boolean(caps?.live_send_allowed && preflight?.ready_for_live_send);

  return (
    <div
      style={{
        marginTop: 8,
        paddingTop: 12,
        borderTop: "1px solid rgba(0,0,0,0.08)",
        display: "grid",
        gap: 8,
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 13 }}>{t("opsSendTitle")}</div>
      <div style={{ fontSize: 12, opacity: 0.8 }}>{t("opsSendDryRunHint")}</div>

      <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
        <span>{recipientLabel}</span>
        <input
          value={recipient}
          onChange={(e) => setRecipient(e.target.value)}
          placeholder={recipientPlaceholder}
          style={{ ...inputStyle, direction: "ltr", textAlign: "left" }}
        />
      </label>
      <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
        <span>{t("opsSendMessage")}</span>
        <textarea
          value={messageText}
          onChange={(e) => setMessageText(e.target.value)}
          rows={2}
          style={inputStyle}
        />
      </label>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          disabled={sending || !sessionReady}
          onClick={() => void handleSend(true)}
          style={{ padding: "8px 12px", borderRadius: 8 }}
        >
          {sending ? t("loading") : t("opsSendDryRunButton")}
        </button>
      </div>

      <div
        style={{
          marginTop: 4,
          padding: 10,
          borderRadius: 8,
          border: "1px solid rgba(185,28,28,0.25)",
          background: "rgba(185,28,28,0.04)",
          display: "grid",
          gap: 8,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 13, color: "#991b1b" }}>
          {t("opsSendLiveTitle")}
        </div>
        <div style={{ fontSize: 12, opacity: 0.85 }}>{t("opsSendLiveWarning")}</div>

        {preflight ? (
          <ul style={{ margin: 0, paddingInlineStart: 18, fontSize: 12, lineHeight: 1.6 }}>
            {preflight.checks.map((check) => (
              <li key={check.key} style={{ color: check.passed ? "#166534" : "#991b1b" }}>
                {check.passed ? "✓" : "✗"} {check.message}
              </li>
            ))}
          </ul>
        ) : null}

        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={liveConfirm}
            onChange={(e) => setLiveConfirm(e.target.checked)}
            disabled={!liveAllowed}
          />
          <span>{t("opsSendLiveConfirmLabel")}</span>
        </label>

        <button
          type="button"
          disabled={sending || !sessionReady || !liveAllowed || !liveConfirm}
          onClick={() => void handleSend(false)}
          style={{
            padding: "8px 12px",
            borderRadius: 8,
            background: liveAllowed && liveConfirm ? "#991b1b" : undefined,
            color: liveAllowed && liveConfirm ? "#fff" : undefined,
            width: "fit-content",
          }}
        >
          {sending ? t("loading") : t("opsSendLiveButton")}
        </button>

        {!caps?.live_send_allowed ? (
          <div style={{ fontSize: 12, color: "#b45309" }}>{t("opsSendLiveEnvBlocked")}</div>
        ) : null}
      </div>

      {!sessionReady ? (
        <div style={{ fontSize: 12, color: "#b45309" }}>{t("opsSendNeedsSession")}</div>
      ) : null}
      {error ? (
        <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
          {error}
        </div>
      ) : null}
      {result ? <div style={{ color: "#166534", fontSize: 13 }}>{result}</div> : null}
    </div>
  );
}
