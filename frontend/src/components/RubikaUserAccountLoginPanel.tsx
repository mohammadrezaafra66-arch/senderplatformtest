import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import { startRubikaUserLogin, verifyRubikaUserLogin, confirmRubikaActivation } from "@/lib/rubika-api";
import { formatResendCountdown, rubikaLoginErrorMessage } from "@/utils/rubika-login-errors";

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
  runtimeStatus?: string | null;
  runtimeLabel?: string | null;
  onRegistered?: () => void;
};

export function RubikaUserAccountLoginPanel({
  accountId,
  accountPhone,
  runtimeStatus,
  runtimeLabel,
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
  const [verifyLabel, setVerifyLabel] = useState<string | null>(null);
  const [activationState, setActivationState] = useState<string | null>(null);
  const [resendIn, setResendIn] = useState(0);

  useEffect(() => {
    if (resendIn <= 0) return;
    const id = window.setInterval(() => {
      setResendIn((current) => (current > 0 ? current - 1 : 0));
    }, 1000);
    return () => window.clearInterval(id);
  }, [resendIn > 0]);

  function beginCooldown(seconds: number | null | undefined) {
    const next = typeof seconds === "number" && seconds > 0 ? Math.ceil(seconds) : 60;
    setResendIn(next);
  }

  function showLoginError(err: unknown) {
    if (err instanceof ApiError) {
      if (err.retryAfterSeconds && err.retryAfterSeconds > 0) {
        setResendIn(Math.ceil(err.retryAfterSeconds));
      }
      setError(rubikaLoginErrorMessage(err.code, err.message));
      return;
    }
    setError(t("actionFailed"));
  }

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
      beginCooldown(result.retry_after_seconds);
    } catch (err) {
      showLoginError(err);
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
      beginCooldown(result.retry_after_seconds);
    } catch (err) {
      showLoginError(err);
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
      setResultGuid(result.guid || null);
      setVerifyLabel(result.runtime_status_label || runtimeLabel || null);
      setActivationState(result.send_activation_state || null);
      setNotice(
        result.send_activation_state === "ACTIVATION_PENDING"
          ? t("rubikaActivationPending")
          : result.message,
      );
      setStage("done");
      onRegistered?.();
    } catch (err) {
      showLoginError(err);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConfirmActivation() {
    setSubmitting(true);
    setError(null);
    try {
      const result = await confirmRubikaActivation(accountId);
      setActivationState(result.send_activation_state);
      setVerifyLabel(result.runtime_status_label || verifyLabel);
      setNotice(t("rubikaActivationConfirmed"));
      onRegistered?.();
    } catch (err) {
      showLoginError(err);
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
    setVerifyLabel(null);
    setActivationState(null);
    setResendIn(0);
    setError(null);
    setNotice(null);
  }

  return (
    <div style={panelInnerStyle}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>{t("rubikaUserLoginTitle")}</strong>
        <span style={badgeStyle(stage === "done" && runtimeStatus === "READY" ? "#166534" : "#b45309")}>
          {stage === "done"
            ? verifyLabel || runtimeLabel || t("rubikaUserLoginSessionActive")
            : stage === "code"
              ? t("runtime_status_OTP_WAITING", { defaultValue: "در انتظار کد" })
              : t("rubikaUserLoginPending")}
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
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button type="submit" disabled={submitting} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {submitting ? t("loading") : t("rubikaUserLoginVerify")}
            </button>
            <button
              type="button"
              disabled={submitting || resendIn > 0}
              onClick={() => {
                void handleStart({ preventDefault() {} } as React.FormEvent);
              }}
              style={{ padding: "8px 12px", borderRadius: 8 }}
            >
              {resendIn > 0
                ? t("rubikaUserLoginResendIn", { time: formatResendCountdown(resendIn) })
                : t("rubikaUserLoginResend")}
            </button>
            <button type="button" onClick={resetFlow} style={{ padding: "8px 12px", borderRadius: 8 }}>
              {t("rubikaUserLoginRestart")}
            </button>
          </div>
        </form>
      ) : null}

      {stage === "done" ? (
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ fontSize: 13 }}>{verifyLabel || runtimeLabel || t("rubikaUserLoginSessionActive")}</div>
          {resultGuid ? (
            <div style={{ fontSize: 13 }}>
              {t("rubikaUserLoginGuid")}: <code>{resultGuid}</code>
            </div>
          ) : null}
          {activationState === "ACTIVATION_PENDING" ? (
            <button
              type="button"
              disabled={submitting}
              onClick={() => void handleConfirmActivation()}
              style={{ padding: "8px 12px", borderRadius: 8 }}
            >
              {submitting ? t("loading") : t("rubikaActivationConfirm")}
            </button>
          ) : null}
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
