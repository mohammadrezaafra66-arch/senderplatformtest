import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "@/lib/api";
import {
  fetchWhatsAppWebPoolStatus,
  fetchWhatsAppWebStatus,
  registerWhatsAppWebSession,
} from "@/lib/accounts-api";
import { OperationalSendTestForm } from "@/components/OperationalSendTestForm";
import type { WhatsAppWebPoolStatus, WhatsAppWebStatus } from "@/types/account";
import { toJalaliDateTime } from "@/utils/jalali";

const panelInnerStyle: React.CSSProperties = {
  marginTop: 8,
  padding: 12,
  borderRadius: 10,
  border: "1px solid rgba(0,0,0,0.1)",
  background: "rgba(0,0,0,0.02)",
  display: "grid",
  gap: 12,
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

type WhatsAppWebPanelProps = {
  accountId: number;
  accountPhone: string | null;
  onRegistered?: () => void;
};

function statusBadge(
  status: WhatsAppWebStatus,
  t: (key: string) => string,
): { label: string; color: string } {
  if (status.linked) {
    return { label: t("waWebStatusLinked"), color: "#166534" };
  }
  if (status.needs_qr) {
    return { label: t("waWebStatusNeedsQr"), color: "#b45309" };
  }
  return { label: t("waWebStatusUnknown"), color: "#6b7280" };
}

export function WhatsAppWebPanel({
  accountId,
  accountPhone,
  onRegistered,
}: WhatsAppWebPanelProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<WhatsAppWebStatus | null>(null);
  const [pool, setPool] = useState<WhatsAppWebPoolStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [waStatus, poolStatus] = await Promise.all([
        fetchWhatsAppWebStatus(accountId),
        fetchWhatsAppWebPoolStatus(),
      ]);
      setStatus(waStatus);
      setPool(poolStatus);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("waWebLoadError"));
    } finally {
      setLoading(false);
    }
  }, [accountId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleRegister() {
    setRegistering(true);
    setError(null);
    setNotice(null);
    try {
      const result = await registerWhatsAppWebSession(accountId, {
        linked: true,
        phone: accountPhone,
      });
      setNotice(result.message);
      await load();
      onRegistered?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRegistering(false);
    }
  }

  if (loading && !status) {
    return <div style={panelInnerStyle}>{t("loading")}</div>;
  }

  const badge = status ? statusBadge(status, t) : null;
  const poolCoversAccount =
    pool?.workers.some((worker) => worker.assigned_account_ids.includes(accountId)) ?? false;

  const linkCommand = `docker compose run --rm core_api python -m workers.whatsapp_web_link --account-id ${accountId}`;

  return (
    <div style={panelInnerStyle}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <strong>{t("waWebTitle")}</strong>
        {badge ? <span style={badgeStyle(badge.color)}>{badge.label}</span> : null}
        {poolCoversAccount ? (
          <span style={badgeStyle("#1d4ed8")}>{t("waWebPoolAssigned")}</span>
        ) : (
          <span style={badgeStyle("#9ca3af")}>{t("waWebPoolNotAssigned")}</span>
        )}
      </div>

      {status ? (
        <div style={{ fontSize: 13, lineHeight: 1.7, opacity: 0.9 }}>
          <div>{status.message}</div>
          <div>
            {t("waWebProfileExists")}: {status.profile_exists ? t("yes") : t("no")}
          </div>
          <div>
            {t("waWebSessionRegistered")}: {status.session_registered ? t("yes") : t("no")}
          </div>
          {status.linked_at ? (
            <div>
              {t("waWebLinkedAt")}: {toJalaliDateTime(status.linked_at)}
            </div>
          ) : null}
          <div style={{ wordBreak: "break-all", fontSize: 12, opacity: 0.75 }}>
            {t("waWebProfileDir")}: {status.profile_dir}
          </div>
        </div>
      ) : null}

      {pool && pool.total > 0 ? (
        <div style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("waWebPoolWorkers")}</div>
          <ul style={{ margin: 0, paddingInlineStart: 18 }}>
            {pool.workers.map((worker) => (
              <li key={worker.hostname}>
                {worker.hostname} — {t("waWebPoolIndex")} {worker.pool_index}/{worker.pool_size}{" "}
                ({worker.assigned_account_ids.join(", ")})
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <div style={{ fontSize: 13, opacity: 0.8 }}>{t("waWebPoolEmpty")}</div>
      )}

      <div style={{ fontSize: 13, lineHeight: 1.6 }}>
        <div style={{ fontWeight: 600 }}>{t("waWebQrStepsTitle")}</div>
        <ol style={{ margin: "6px 0 0", paddingInlineStart: 20 }}>
          <li>{t("waWebQrStep1")}</li>
          <li>{t("waWebQrStep2")}</li>
          <li>{t("waWebQrStep3")}</li>
        </ol>
        <pre
          style={{
            marginTop: 8,
            padding: 10,
            borderRadius: 8,
            background: "rgba(0,0,0,0.06)",
            fontSize: 12,
            overflowX: "auto",
            direction: "ltr",
            textAlign: "left",
          }}
        >
          {linkCommand}
        </pre>
      </div>

      {error ? (
        <div role="alert" style={{ color: "#991b1b", fontSize: 13 }}>
          {error}
        </div>
      ) : null}
      {notice ? <div style={{ color: "#166534", fontSize: 13 }}>{notice}</div> : null}

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          style={{ padding: "8px 12px", borderRadius: 8 }}
        >
          {loading ? t("loading") : t("refresh")}
        </button>
        <button
          type="button"
          onClick={() => void handleRegister()}
          disabled={registering}
          style={{ padding: "8px 12px", borderRadius: 8 }}
        >
          {registering ? t("loading") : t("waWebMarkLinked")}
        </button>
      </div>

      <OperationalSendTestForm
        accountId={accountId}
        defaultRecipient={accountPhone}
        recipientLabel={t("opsSendRecipientPhone")}
        recipientPlaceholder={t("opsSendRecipientPhonePlaceholder")}
        sessionReady={Boolean(status?.linked && status?.profile_exists)}
      />
    </div>
  );
}
