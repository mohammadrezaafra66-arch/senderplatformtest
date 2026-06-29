import { useEffect, useState } from "react";
import { apiFetch, ApiError } from "@/lib/api";

interface DelaySettings {
  account_id: number;
  min_delay_seconds: number;
  max_delay_seconds: number;
  floor_delay_seconds: number;
  risk_level: "safe" | "medium" | "high" | "blocked";
  updated_at: string;
}

type RiskInfo = {
  label: string;
  bg: string;
  text: string;
  border: string;
  warn: string;
};

const RISK_CONFIG: Record<DelaySettings["risk_level"], RiskInfo> = {
  safe: {
    label: "امن",
    bg: "#f0faf4",
    text: "#1a7a4a",
    border: "#b6e8cc",
    warn: "",
  },
  medium: {
    label: "متوسط",
    bg: "#fffbeb",
    text: "#8a6000",
    border: "#f5d98a",
    warn: "احتمال محدودیت واتساپ وجود دارد",
  },
  high: {
    label: "پرریسک",
    bg: "#fff7ed",
    text: "#9a3412",
    border: "#fdba74",
    warn: "⚠️ خطر بلاک شدن اکانت بالاست",
  },
  blocked: {
    label: "ممنوع",
    bg: "#fff0f0",
    text: "#b02020",
    border: "#f5b8b8",
    warn: "🚫 کمتر از ۱۰ ثانیه مجاز نیست",
  },
};

function msgsPerHour(minD: number, maxD: number): number {
  const avg = (minD + maxD) / 2;
  return avg > 0 ? Math.floor(3600 / avg) : 0;
}

function calcRisk(min: number): DelaySettings["risk_level"] {
  if (min < 10) return "blocked";
  if (min < 20) return "high";
  if (min < 45) return "medium";
  return "safe";
}

export default function DelaySetting({ accountId }: { accountId: number }) {
  const [settings, setSettings] = useState<DelaySettings | null>(null);
  const [minD, setMinD] = useState(45);
  const [maxD, setMaxD] = useState(90);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const res = await apiFetch(`/accounts/${accountId}/send-settings`);
        const d = (await res.json()) as DelaySettings;
        if (!active) return;
        setSettings(d);
        setMinD(d.min_delay_seconds);
        setMaxD(d.max_delay_seconds);
      } catch {
        if (active) setError("خطا در بارگذاری تنظیمات");
      }
    })();
    return () => {
      active = false;
    };
  }, [accountId]);

  async function handleSave() {
    if (minD < 10) {
      setError("حداقل delay نمی‌تواند کمتر از ۱۰ ثانیه باشد");
      return;
    }
    if (maxD < minD) {
      setError("حداکثر باید بیشتر از حداقل باشد");
      return;
    }
    setError("");
    setSaving(true);
    try {
      const res = await apiFetch(`/accounts/${accountId}/send-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_delay_seconds: minD, max_delay_seconds: maxD }),
      });
      const d = (await res.json()) as DelaySettings;
      setSettings(d);
      setSaved(true);
      setTimeout(() => setSaved(false), 2500);
    } catch (e: unknown) {
      if (e instanceof ApiError) setError(e.message);
      else setError(e instanceof Error ? e.message : "خطا در ذخیره");
    } finally {
      setSaving(false);
    }
  }

  const currentRisk = calcRisk(minD);
  const risk = RISK_CONFIG[currentRisk];
  const msgs = msgsPerHour(minD, maxD);

  const labelRow: React.CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "0.875rem",
    color: "#555",
  };
  const scaleRow: React.CSSProperties = {
    display: "flex",
    justifyContent: "space-between",
    fontSize: "0.7rem",
    color: "#9ca3af",
    marginTop: 2,
  };
  const statBox: React.CSSProperties = { textAlign: "center" };
  const statLabel: React.CSSProperties = { fontSize: "0.7rem", color: "#6b7280" };

  return (
    <div
      dir="rtl"
      style={{
        background: "#fff",
        border: "1px solid #e5e5e5",
        borderRadius: 12,
        padding: "18px",
        marginTop: 16,
        fontFamily: "inherit",
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <h3 style={{ fontWeight: 600, fontSize: "1rem", color: "#111", margin: 0 }}>
          ⏱ تنظیمات تأخیر ارسال
        </h3>
        <span
          style={{
            fontSize: "0.75rem",
            fontWeight: 600,
            padding: "4px 10px",
            borderRadius: 999,
            background: risk.bg,
            color: risk.text,
            border: `1px solid ${risk.border}`,
          }}
        >
          {risk.label}
        </span>
      </div>

      {risk.warn && (
        <div
          style={{
            fontSize: "0.8rem",
            borderRadius: 8,
            padding: "8px 12px",
            background: risk.bg,
            color: risk.text,
            border: `1px solid ${risk.border}`,
          }}
        >
          {risk.warn}
        </div>
      )}

      {/* Min delay slider */}
      <div>
        <div style={labelRow}>
          <span>حداقل تأخیر</span>
          <span style={{ fontFamily: "monospace", fontWeight: 500 }}>
            {minD} ثانیه
          </span>
        </div>
        <input
          type="range"
          min={10}
          max={180}
          value={minD}
          onChange={(e) => {
            const v = Number(e.target.value);
            setMinD(v);
            if (maxD < v) setMaxD(v + 10);
          }}
          style={{ width: "100%", accentColor: "#2563eb" }}
        />
        <div style={scaleRow}>
          <span>۱۰s (کف)</span>
          <span>۴۵s (امن)</span>
          <span>۱۸۰s</span>
        </div>
      </div>

      {/* Max delay slider */}
      <div>
        <div style={labelRow}>
          <span>حداکثر تأخیر</span>
          <span style={{ fontFamily: "monospace", fontWeight: 500 }}>
            {maxD} ثانیه
          </span>
        </div>
        <input
          type="range"
          min={minD}
          max={300}
          value={maxD}
          onChange={(e) => setMaxD(Number(e.target.value))}
          style={{ width: "100%", accentColor: "#2563eb" }}
        />
        <div style={scaleRow}>
          <span>{minD}s</span>
          <span>۳۰۰s</span>
        </div>
      </div>

      {/* Stats */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: 12,
          background: "#f9fafb",
          borderRadius: 8,
          padding: 12,
        }}
      >
        <div style={statBox}>
          <div style={statLabel}>میانگین تأخیر</div>
          <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "#374151" }}>
            {Math.round((minD + maxD) / 2)}s
          </div>
        </div>
        <div style={statBox}>
          <div style={statLabel}>پیام در ساعت</div>
          <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "#2563eb" }}>
            ~{msgs}
          </div>
        </div>
        <div style={statBox}>
          <div style={statLabel}>پیام در روز</div>
          <div style={{ fontSize: "0.875rem", fontWeight: 600, color: "#2563eb" }}>
            ~{msgs * 8}
          </div>
        </div>
      </div>

      {error && (
        <div
          style={{
            fontSize: "0.8rem",
            color: "#b02020",
            background: "#fff0f0",
            border: "1px solid #f5b8b8",
            borderRadius: 8,
            padding: "8px 12px",
          }}
        >
          {error}
        </div>
      )}

      <button
        type="button"
        onClick={handleSave}
        disabled={saving}
        style={{
          width: "100%",
          padding: "0.5rem",
          borderRadius: 8,
          border: "none",
          fontSize: "0.875rem",
          fontWeight: 500,
          color: "#fff",
          background: saving ? "#9ca3af" : "#2563eb",
          cursor: saving ? "not-allowed" : "pointer",
        }}
      >
        {saving ? "در حال ذخیره..." : saved ? "✅ ذخیره شد" : "ذخیره تنظیمات"}
      </button>

      {settings && (
        <div style={{ fontSize: "0.7rem", color: "#9ca3af", textAlign: "left" }}>
          آخرین تغییر: {new Date(settings.updated_at).toLocaleString("fa-IR")}
        </div>
      )}
    </div>
  );
}
