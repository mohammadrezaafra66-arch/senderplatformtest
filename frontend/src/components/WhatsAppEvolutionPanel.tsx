import React, { useState, useEffect, useCallback } from "react";
import {
  fetchEvolutionInstanceStatus,
  startEvolutionQrLink,
  disconnectEvolutionInstance,
} from "@/lib/accounts-api";
import type { EvolutionInstanceStatus } from "@/types/account";
import TooltipHint from "@/components/TooltipHint";
import { OperationalSendTestForm } from "@/components/OperationalSendTestForm";

interface WhatsAppEvolutionPanelProps {
  accountId: number;
  accountLabel?: string;
  onAssignProxy?: () => void;
}

const colors = {
  green: { bg: "#f0faf4", text: "#1a7a4a", border: "#b6e8cc" },
  orange: { bg: "#fffbeb", text: "#8a6000", border: "#f5d98a" },
  gray: { bg: "#f2f2f2", text: "#555", border: "#ddd" },
  red: { bg: "#fff0f0", text: "#b02020", border: "#f5b8b8" },
  yellow: { bg: "#fffbeb", text: "#7a5800", border: "#f5d98a" },
  blue: { bg: "#eff6ff", text: "#1d4ed8", border: "#bfdbfe" },
};

export default function WhatsAppEvolutionPanel({
  accountId,
  accountLabel,
  onAssignProxy,
}: WhatsAppEvolutionPanelProps) {
  const [status, setStatus] = useState<EvolutionInstanceStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(false);
  const [localQr, setLocalQr] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetchEvolutionInstanceStatus(accountId);
      setStatus(data);
      if (data.state === "open") {
        setPolling(false);
        setLocalQr(null);
      }
      setError(null);
    } catch {
      setError("خطا در دریافت وضعیت");
    }
  }, [accountId]);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    if (!polling) return;
    const interval = setInterval(async () => {
      try {
        const data = await fetchEvolutionInstanceStatus(accountId);
        setStatus(data);
        if (data.state === "open") {
          setPolling(false);
          setLocalQr(null);
        }
      } catch {
        setError("خطا در دریافت وضعیت");
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [polling, accountId]);

  const handleGetQR = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await startEvolutionQrLink(accountId);
      if (result.qr_code) setLocalQr(result.qr_code);
      setPolling(true);
    } catch {
      setError("خطا در دریافت QR");
    } finally {
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    if (!window.confirm("آیا از قطع اتصال این اکانت اطمینان دارید؟")) return;
    setLoading(true);
    try {
      await disconnectEvolutionInstance(accountId);
      setPolling(false);
      setLocalQr(null);
      const updated = await fetchEvolutionInstanceStatus(accountId);
      setStatus(updated);
    } catch {
      setError("خطا در قطع اتصال");
    } finally {
      setLoading(false);
    }
  };

  const displayState = (() => {
    if (!status || !status.proxy_assigned) return "no_proxy";
    if (status.state === "open" || status.connected === true) return "connected";
    if (status.state === "connecting" || polling) return "connecting";
    return "disconnected";
  })();

  const badgeConfig: Record<
    string,
    { palette: typeof colors.green; label: string; showSpinner?: boolean }
  > = {
    no_proxy: { palette: colors.red, label: "قطع شده — بدون Proxy" },
    disconnected: { palette: colors.gray, label: "قطع شده — با Proxy" },
    connecting: {
      palette: colors.orange,
      label: "در حال اتصال",
      showSpinner: true,
    },
    connected: { palette: colors.green, label: "متصل" },
  };

  const badge = badgeConfig[displayState];

  const btnBase: React.CSSProperties = {
    padding: "0.5rem 1rem",
    border: "none",
    borderRadius: "6px",
    fontFamily: "inherit",
    fontSize: "0.875rem",
    cursor: "pointer",
  };

  return (
    <>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.2} }
      `}</style>

      <div
        dir="rtl"
        style={{
          background: "#fff",
          border: "1px solid #e5e5e5",
          borderRadius: 12,
          padding: "20px 18px",
          fontFamily: "inherit",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: 16,
            gap: 12,
          }}
        >
          <div>
            <div style={{ fontWeight: 600, fontSize: "1rem", color: "#111" }}>
              {accountLabel ?? `اکانت ${accountId}`}
            </div>
            <div style={{ fontSize: "0.8rem", color: "#888", marginTop: 2 }}>
              واتساپ — Evolution API
            </div>
          </div>

          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: 999,
                fontSize: "0.75rem",
                fontWeight: 600,
                background: badge.palette.bg,
                color: badge.palette.text,
                border: `1px solid ${badge.palette.border}`,
                whiteSpace: "nowrap",
              }}
            >
              {badge.showSpinner && (
                <span
                  style={{
                    display: "inline-block",
                    width: 12,
                    height: 12,
                    border: `2px solid ${badge.palette.border}`,
                    borderTopColor: badge.palette.text,
                    borderRadius: "50%",
                    animation: "spin 0.8s linear infinite",
                  }}
                />
              )}
              {badge.label}
            </span>
            <TooltipHint text="وضعیت فعلی اتصال این اکانت به Evolution API — برای اتصال ابتدا باید Proxy تخصیص دهید" />
          </span>
        </div>

        {/* Proxy status */}
        {status?.proxy_assigned ? (
          <div
            style={{
              background: colors.green.bg,
              color: colors.green.text,
              border: `1px solid ${colors.green.border}`,
              borderRadius: 8,
              padding: "10px 12px",
              marginBottom: 14,
              fontSize: "0.875rem",
            }}
          >
            ✓ Proxy ثابت تخصیص‌یافته
          </div>
        ) : (
          <div
            style={{
              background: colors.red.bg,
              color: colors.red.text,
              border: `1px solid ${colors.red.border}`,
              borderRadius: 8,
              padding: "10px 12px",
              marginBottom: 14,
              fontSize: "0.875rem",
            }}
          >
            ⚠ بدون Proxy — قبل از اتصال باید Proxy تخصیص دهید
          </div>
        )}

        {/* Connected info */}
        {displayState === "connected" && (
          <div
            style={{
              background: colors.green.bg,
              border: `1px solid ${colors.green.border}`,
              borderRadius: 8,
              padding: "12px 14px",
              marginBottom: 14,
              fontSize: "0.875rem",
              color: colors.green.text,
            }}
          >
            {status?.phone && (
              <div style={{ marginBottom: 6 }}>
                شماره تلفن:{" "}
                <strong style={{ direction: "ltr", display: "inline-block" }}>
                  {status.phone}
                </strong>
              </div>
            )}
            {status?.connected_at && (
              <div style={{ fontSize: "0.8rem", color: "#555" }}>
                آخرین اتصال:{" "}
                {new Date(status.connected_at).toLocaleString("fa-IR")}
              </div>
            )}
          </div>
        )}

        {/* QR section */}
        {displayState === "connecting" && (
          <div style={{ textAlign: "center", marginBottom: 16 }}>
            {localQr ? (
              <img
                src={
                  localQr.startsWith("data:")
                    ? localQr
                    : `data:image/png;base64,${localQr}`
                }
                width={256}
                height={256}
                alt="WhatsApp QR Code"
                style={{
                  display: "block",
                  margin: "0 auto",
                  borderRadius: 4,
                  border: "2px solid #f5d98a",
                }}
              />
            ) : loading ? (
              <div
                style={{
                  width: 256,
                  height: 256,
                  margin: "0 auto",
                  background: "#f2f2f2",
                  borderRadius: 4,
                  border: "1px solid #ddd",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "#555",
                  fontSize: "0.875rem",
                }}
              >
                در حال بارگذاری QR...
              </div>
            ) : null}

            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 6,
                marginTop: 10,
                fontSize: "0.8rem",
                color: colors.orange.text,
              }}
            >
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: colors.orange.text,
                  animation: "blink 1.2s ease-in-out infinite",
                }}
              />
              هر ۳ ثانیه بررسی می‌شود...
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div
            style={{
              background: colors.red.bg,
              color: colors.red.text,
              border: `1px solid ${colors.red.border}`,
              borderRadius: 8,
              padding: "10px 12px",
              marginBottom: 14,
              fontSize: "0.875rem",
            }}
          >
            {error}
          </div>
        )}

        {/* Buttons */}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {!status?.proxy_assigned && onAssignProxy && (
            <button
              type="button"
              onClick={onAssignProxy}
              style={{
                ...btnBase,
                background: colors.blue.text,
                color: "#fff",
              }}
            >
              تخصیص Proxy
            </button>
          )}
          {!status?.proxy_assigned && onAssignProxy && (
            <TooltipHint text="برای اتصال واتساپ، باید یک Proxy ثابت به این اکانت تخصیص دهید تا IP شما ثابت بماند" />
          )}

          {status?.proxy_assigned && displayState === "disconnected" && (
            <button
              type="button"
              onClick={handleGetQR}
              disabled={loading}
              style={{
                ...btnBase,
                background: loading ? "#9ca3af" : "#2563eb",
                color: "#fff",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              دریافت QR Code
            </button>
          )}

          {displayState === "connecting" && (
            <button
              type="button"
              disabled
              style={{
                ...btnBase,
                background: "#9ca3af",
                color: "#fff",
                cursor: "not-allowed",
              }}
            >
              {loading ? "در حال پردازش..." : "دریافت QR Code"}
            </button>
          )}

          {displayState === "connected" && (
            <button
              type="button"
              onClick={handleDisconnect}
              disabled={loading}
              style={{
                ...btnBase,
                background: loading ? "#9ca3af" : "#dc2626",
                color: "#fff",
                cursor: loading ? "not-allowed" : "pointer",
              }}
            >
              {loading ? "در حال پردازش..." : "قطع اتصال"}
            </button>
          )}

          <button
            type="button"
            onClick={() => void loadStatus()}
            disabled={loading}
            style={{
              ...btnBase,
              background: "#6b7280",
              color: "#fff",
              cursor: loading ? "not-allowed" : "pointer",
            }}
          >
            به‌روزرسانی
          </button>
          <TooltipHint text="وضعیت اتصال را از سرور دریافت و نمایش می‌دهد" />
        </div>

        {displayState === "connected" && (
          <div style={{ marginTop: 16 }}>
            <OperationalSendTestForm
              accountId={accountId}
              defaultRecipient={status?.phone}
              recipientLabel="شماره گیرنده"
              recipientPlaceholder="مثلاً 98912xxxxxxx"
              sessionReady={displayState === "connected"}
            />
          </div>
        )}
      </div>
    </>
  );
}
