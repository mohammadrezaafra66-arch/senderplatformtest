import { useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import { confirmRubikaActivation } from "@/lib/rubika-api";
import type { RubikaActivationStatusResult } from "@/types/rubika";

const cardStyle: React.CSSProperties = {
  marginTop: 8,
  padding: 12,
  borderRadius: 10,
  border: "1px solid rgba(180,83,9,0.35)",
  background: "rgba(180,83,9,0.06)",
  display: "grid",
  gap: 8,
};

const readyStyle: React.CSSProperties = {
  display: "inline-block",
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: "#166534",
  color: "#fff",
};

const pendingStyle: React.CSSProperties = {
  display: "inline-block",
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  background: "#b45309",
  color: "#fff",
};

export function isActivationReady(activation?: RubikaActivationStatusResult | null): boolean {
  const state = (activation?.send_activation_state || "").toUpperCase();
  const status = (activation?.status || "").toLowerCase();
  return state === "READY_TO_SEND" || status === "confirmed" || status === "ready_to_send";
}

export function needsManagerActivationCard(
  activation?: RubikaActivationStatusResult | null,
): boolean {
  if (!activation) return false;
  const state = (activation.send_activation_state || "").toUpperCase();
  const status = (activation.status || "").toLowerCase();
  if (isActivationReady(activation)) return false;
  if (!activation.status) return false;
  return (
    state === "ACTIVATION_PENDING" ||
    status === "challenge_sent" ||
    status === "pending" ||
    status === "failed"
  );
}

type RubikaActivationCardProps = {
  accountId: number;
  activation?: RubikaActivationStatusResult | null;
  canConfirm?: boolean;
  onConfirmed?: () => void;
};

export function RubikaActivationBadge({
  activation,
}: {
  activation?: RubikaActivationStatusResult | null;
}) {
  const { t } = useTranslation();
  if (isActivationReady(activation)) {
    return <span style={readyStyle}>{t("rubikaActivationReadyToSend")}</span>;
  }
  if (needsManagerActivationCard(activation)) {
    return <span style={pendingStyle}>{t("rubikaActivationPendingLabel")}</span>;
  }
  return <span style={{ opacity: 0.55 }}>{t("rubikaActivationNone")}</span>;
}

export function RubikaActivationCard({
  accountId,
  activation,
  canConfirm = false,
  onConfirmed,
}: RubikaActivationCardProps) {
  const { t } = useTranslation();
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!needsManagerActivationCard(activation)) {
    return null;
  }

  async function handleConfirm() {
    setSubmitting(true);
    setError(null);
    try {
      const payload = activation?.confirm_code
        ? { confirm_code: activation.confirm_code }
        : {};
      const result = await confirmRubikaActivation(accountId, payload);
      if (!result.success) {
        setError(result.message || t("actionFailed"));
        return;
      }
      onConfirmed?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={cardStyle}>
      <strong>{t("rubikaActivationTitle")}</strong>
      <div style={{ fontSize: 13 }}>{t("rubikaActivationPending")}</div>
      {activation?.manager_phone ? (
        <div style={{ fontSize: 13 }}>
          {t("rubikaActivationManagerPhone")}:{" "}
          <span dir="ltr" style={{ fontWeight: 600 }}>
            {activation.manager_phone}
          </span>
        </div>
      ) : null}
      {activation?.test_error ? (
        <div style={{ fontSize: 13, color: "#991b1b" }}>
          {t("rubikaActivationFailedLabel")}: {activation.test_error}
        </div>
      ) : null}
      {canConfirm ? (
        <div>
          <button
            type="button"
            disabled={submitting}
            onClick={() => void handleConfirm()}
            style={{ padding: "8px 12px", borderRadius: 8 }}
          >
            {submitting ? t("loading") : t("rubikaActivationConfirm")}
          </button>
        </div>
      ) : null}
      {error ? (
        <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
          {error}
        </div>
      ) : null}
    </div>
  );
}
