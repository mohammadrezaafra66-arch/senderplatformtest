import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import { startRubikaUserLogin, verifyRubikaUserLogin } from "@/lib/rubika-api";

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

type Stage = "phone" | "pass_key" | "code" | "done";

type RubikaUserAccountLoginPanelProps = {
  accountId: number;
  accountPhone?: string | null;
  onRegistered?: () => void;
};

export function RubikaUserAccountLoginPanel({
  accountId,
  accountPhone,
  onRegistered,
}: RubikaUserAccountLoginPanelProps) {
  const { t } = useTranslation();
  const [stage, setStage] = useState<Stage>("phone");
  const [phone, setPhone] = useState(accountPhone ?? "");
  const [passKey, setPassKey] = useState("");
  const [passKeyHint, setPassKeyHint] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [registrationToken, setRegistrationToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resultGuid, setResultGuid] = useState<string | null>(null);

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!phone.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const result = await startRubikaUserLogin(accountId, { phone_number: phone.trim() });
      setRegistrationToken(result.registration_token);
      if (result.stage === "pass_key_required") {
        setPassKeyHint(result.hint_pass_key ?? null);
        setStage("pass_key");
      } else {
        setStage("code");
      }
      setNotice(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmitPassKey(e: React.FormEvent) {
    e.preventDefault();
    if (!passKey.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const result = await startRubikaUserLogin(accountId, {
        registration_token: registrationToken,
        pass_key: passKey.trim(),
      });
      setRegistrationToken(result.registration_token);
      if (result.stage === "pass_key_required") {
        setPassKeyHint(result.hint_pass_key ?? null);
      } else {
        setStage("code");
      }
      setNotice(result.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleVerify(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) {
      setError(t("requiredFields"));
      return;
    }
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const result = await verifyRubikaUserLogin(accountId, {
        registration_token: registrationToken,
        phone_code: code.trim(),
      });
      setResultGuid(result.guid);
      setNotice(result.message);
      setStage("done");
      onRegistered?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  function resetFlow() {
    setStage("phone");
    setCode("");
    setPassKey("");
    setPassKeyHint(null);
    setRegistrationToken("");
    setResultGuid(null);
    setError(null);
    setNotice(null);
  }

  return (
    <div style={panelInnerStyle}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>{t("rubikaUserLoginTitle")}</strong>
        <span style={badgeStyle(stage === "done" ? "#166534" : "#b45309")}>
          {stage === "done" ? t("rubikaUserLoginDone") : t("rubikaUserLoginPending")}
        </span>
      </div>

      <div style={{ fontSize: 12, opacity: 0.75 }}>{t("rubikaUserLoginHint")}</div>

      {stage === "phone" ? (
        <form onSubmit={(e) => void handleStart(e)} style={{ display: "grid", gap: 8 }}>
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            <span>{t("rubikaUserLoginPhoneLabel")}</span>
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="09120000000"
              style={inputStyle}
            />
          </label>
          <div>
            <button type="submit" disabled={submitting} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {submitting ? t("loading") : t("rubikaUserLoginSendCode")}
            </button>
          </div>
        </form>
      ) : null}

      {stage === "pass_key" ? (
        <form onSubmit={(e) => void handleSubmitPassKey(e)} style={{ display: "grid", gap: 8 }}>
          {passKeyHint ? (
            <div style={{ fontSize: 13 }}>
              {t("rubikaUserLoginPassKeyHint")}: {passKeyHint}
            </div>
          ) : null}
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            <span>{t("rubikaUserLoginPassKeyLabel")}</span>
            <input
              value={passKey}
              onChange={(e) => setPassKey(e.target.value)}
              style={inputStyle}
            />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={submitting} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {submitting ? t("loading") : t("rubikaUserLoginSubmit")}
            </button>
            <button type="button" onClick={resetFlow} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {t("rubikaUserLoginRestart")}
            </button>
          </div>
        </form>
      ) : null}

      {stage === "code" ? (
        <form onSubmit={(e) => void handleVerify(e)} style={{ display: "grid", gap: 8 }}>
          <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
            <span>{t("rubikaUserLoginCodeLabel")}</span>
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="12345"
              style={inputStyle}
            />
          </label>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="submit" disabled={submitting} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {submitting ? t("loading") : t("rubikaUserLoginVerify")}
            </button>
            <button type="button" onClick={resetFlow} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {t("rubikaUserLoginRestart")}
            </button>
          </div>
        </form>
      ) : null}

      {stage === "done" ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13 }}>
            {t("rubikaUserLoginGuid")}: <code>{resultGuid}</code>
          </div>
          <div>
            <button type="button" onClick={resetFlow} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {t("rubikaUserLoginReconnect")}
            </button>
          </div>
        </div>
      ) : null}

      {error ? (
        <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
          {error}
        </div>
      ) : null}
      {notice ? <div style={{ color: "#166534", fontSize: 13 }}>{notice}</div> : null}
    </div>
  );
}
