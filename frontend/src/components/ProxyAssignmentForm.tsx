import React, { useState } from "react";
import { assignAccountProxy } from "@/lib/accounts-api";
import type { ProxyAssignRequest } from "@/types/account";
import TooltipHint from "@/components/TooltipHint";

interface ProxyAssignmentFormProps {
  accountId: number;
  accountLabel?: string;
  isConnected: boolean;
  onSaved?: () => void;
  onAssign?: (proxyData: ProxyAssignRequest) => Promise<void>;
}

const colors = {
  green: { bg: "#f0faf4", text: "#1a7a4a", border: "#b6e8cc" },
  yellow: { bg: "#fffbeb", text: "#7a5800", border: "#f5d98a" },
  red: { bg: "#fff0f0", text: "#b02020", border: "#f5b8b8" },
  blue: { bg: "#eff6ff", text: "#1d4ed8", border: "#bfdbfe" },
  gray: { bg: "#f2f2f2", text: "#555", border: "#ddd" },
};

export default function ProxyAssignmentForm({
  accountId,
  accountLabel,
  isConnected,
  onSaved,
  onAssign,
}: ProxyAssignmentFormProps) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState("");
  const [protocol, setProtocol] = useState<"http" | "socks5">("http");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [poolId, setPoolId] = useState("");
  const [forceConfirm, setForceConfirm] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    if (!host.trim() || !port.trim()) {
      setError("لطفاً Host و Port را وارد کنید");
      return;
    }
    if (isConnected && !forceConfirm) {
      setError("برای تغییر Proxy روی اکانت متصل، تیک تأیید را بزنید");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const proxy: ProxyAssignRequest = {
        host: host.trim(),
        port: parseInt(port, 10),
        protocol,
        username: username.trim() || undefined,
        password: password || undefined,
        pool_id: poolId.trim() || undefined,
      };
      if (onAssign) {
        await onAssign(proxy);
      } else {
        await assignAccountProxy(accountId, proxy);
      }
      setSaved(true);
      setForceConfirm(false);
      onSaved?.();
      setTimeout(() => setSaved(false), 3000);
    } catch {
      setError("خطا در ذخیره‌سازی Proxy. دوباره تلاش کنید.");
    } finally {
      setSaving(false);
    }
  };

  const fieldLabelStyle: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 700,
    color: "#555",
    marginBottom: 5,
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "8px 10px",
    borderRadius: 7,
    border: "1px solid #ddd",
    fontFamily: "Tahoma Arial sans-serif",
    fontSize: 13,
    color: "#333",
    background: "#fafafa",
  };

  const submitStyle = (() => {
    if (saved) {
      return {
        width: "100%",
        marginTop: 16,
        padding: "11px 0",
        borderRadius: 8,
        fontFamily: "Tahoma Arial sans-serif",
        fontSize: 14,
        fontWeight: 700,
        background: colors.green.bg,
        color: colors.green.text,
        border: `1px solid ${colors.green.border}`,
        cursor: "default",
      } satisfies React.CSSProperties;
    }

    const blocked = isConnected && !forceConfirm;
    const disabled = saving || blocked;
    return {
      width: "100%",
      marginTop: 16,
      padding: "11px 0",
      borderRadius: 8,
      border: "none",
      fontFamily: "Tahoma Arial sans-serif",
      fontSize: 14,
      fontWeight: 700,
      background: disabled ? "#ccc" : colors.green.text,
      color: disabled ? "#888" : "#fff",
      cursor: disabled ? "not-allowed" : "pointer",
    } satisfies React.CSSProperties;
  })();

  const submitLabel = (() => {
    if (saving) return "در حال ذخیره...";
    if (saved) return "✓ Proxy با موفقیت ذخیره شد";
    if (isConnected && !forceConfirm) return "تیک تأیید را بزنید";
    return `ذخیره Proxy برای اکانت ${accountId}`;
  })();

  return (
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
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
        }}
      >
        <div style={{ fontWeight: 700, fontSize: 15 }}>
          تخصیص Proxy به اکانت{accountLabel ? ` — ${accountLabel}` : ""}
        </div>
        <span
          style={{
            background: colors.blue.bg,
            color: colors.blue.text,
            border: `1px solid ${colors.blue.border}`,
            borderRadius: 20,
            padding: "3px 10px",
            fontSize: 12,
            fontWeight: 700,
            whiteSpace: "nowrap",
          }}
        >
          فقط ادمین
        </span>
      </div>

      <hr style={{ border: "none", borderTop: "1px solid #eee", margin: "14px 0" }} />

      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 2 }}>
          <div style={fieldLabelStyle}>
            آدرس Host{" "}
            <TooltipHint text={"آدرس سرور Proxy که از ارائه‌دهنده‌ی Proxy دریافت کرده‌اید.\nمثال: proxy.example.com یا 45.132.10.55\nاین آدرس را از پنل کاربری سرویس Proxy خود کپی کنید."} />
          </div>
          <input
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="proxy.example.com"
            style={inputStyle}
          />
        </div>
        <div style={{ flex: 1 }}>
          <div style={fieldLabelStyle}>
            پورت{" "}
            <TooltipHint text={"شماره پورت اتصال به Proxy — معمولاً یکی از این اعداد است:\n- 8080 (رایج‌ترین)\n- 3128\n- 1080\nاین عدد را از پنل کاربری سرویس Proxy خود پیدا کنید."} />
          </div>
          <input
            value={port}
            onChange={(e) => setPort(e.target.value)}
            type="number"
            placeholder="8080"
            style={inputStyle}
          />
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={fieldLabelStyle}>
            پروتکل{" "}
            <TooltipHint text={"نوع اتصال به Proxy:\n- HTTP — برای واتساپ توصیه می‌شود\n- SOCKS5 — اگر ارائه‌دهنده فقط SOCKS5 دارد\nاگر مطمئن نیستید، HTTP را انتخاب کنید."} />
          </div>
          <select
            value={protocol}
            onChange={(e) => setProtocol(e.target.value as "http" | "socks5")}
            style={inputStyle}
          >
            <option value="http">HTTP</option>
            <option value="socks5">SOCKS5</option>
          </select>
        </div>
        <div style={{ flex: 1 }}>
          <div style={fieldLabelStyle}>
            شناسه Pool <span style={{ color: "#aaa", fontWeight: 400 }}>(اختیاری)</span>{" "}
            <TooltipHint text={"یک نام دلخواه برای گروه‌بندی Proxy ها.\nمثال: pool-tehran-01 یا proxy-group-A\nاگر فقط یک Proxy دارید، این فیلد را خالی بگذارید."} />
          </div>
          <input
            value={poolId}
            onChange={(e) => setPoolId(e.target.value)}
            placeholder="pool-ir-01"
            style={inputStyle}
          />
        </div>
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={fieldLabelStyle}>
            نام کاربری{" "}
            <TooltipHint text={"نام کاربری احراز هویت Proxy.\nاین اطلاعات را از ارائه‌دهنده‌ی Proxy دریافت می‌کنید.\nاگر Proxy رایگان یا بدون رمز است، این فیلد را خالی بگذارید."} />
          </div>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="username"
            style={inputStyle}
          />
        </div>
        <div style={{ flex: 1 }}>
          <div style={fieldLabelStyle}>
            رمز عبور{" "}
            <TooltipHint text={"رمز عبور احراز هویت Proxy.\nاین اطلاعات را از ارائه‌دهنده‌ی Proxy دریافت می‌کنید.\nرمز عبور به‌صورت رمزنگاری‌شده ذخیره می‌شود و قابل مشاهده نیست."} />
          </div>
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            placeholder="••••••••"
            style={inputStyle}
          />
        </div>
      </div>

      <div
        style={{
          marginTop: 14,
          background: colors.yellow.bg,
          border: `1px solid ${colors.yellow.border}`,
          borderRadius: 8,
          padding: "10px 14px",
          fontSize: 13,
          color: colors.yellow.text,
          lineHeight: 1.7,
          display: "flex",
          gap: 8,
          alignItems: "flex-start",
        }}
      >
        <span style={{ fontSize: 16 }}>⚠</span>
        <span>
          تغییر Proxy اکانتی که session فعال دارد، ریسک مسدود شدن را افزایش می‌دهد. این
          عملیات را فقط در صورت ضرورت انجام دهید.
        </span>
      </div>

      {isConnected && (
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginTop: 14,
            fontSize: 13,
            color: "#333",
          }}
        >
          <input
            type="checkbox"
            checked={forceConfirm}
            onChange={(e) => setForceConfirm(e.target.checked)}
            style={{ width: 16, height: 16 }}
          />
          از تغییر Proxy روی اکانت متصل اطمینان دارم
        </label>
      )}

      {error && (
        <div
          style={{
            background: colors.red.bg,
            color: colors.red.text,
            border: `1px solid ${colors.red.border}`,
            borderRadius: 8,
            padding: "8px 12px",
            marginTop: 10,
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}

      <button
        type="button"
        onClick={() => void handleSubmit()}
        disabled={saving || (isConnected && !forceConfirm)}
        style={submitStyle}
      >
        {submitLabel}
      </button>
      <TooltipHint text={"با کلیک این دکمه، اطلاعات Proxy به این اکانت واتساپ تخصیص داده می‌شود.\nپس از ذخیره، می‌توانید اتصال واتساپ را برقرار کنید.\nتوجه: تغییر Proxy روی اکانت متصل، ریسک قطع اتصال دارد."} />
    </div>
  );
}
