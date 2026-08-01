import { useState, useEffect, useRef, useCallback } from "react";
import { Layout } from "@/components/Layout";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

async function apiFetch(path: string, opts: RequestInit = {}) {
  const token = typeof window !== "undefined" ? window.sessionStorage.getItem("mmp.access_token") : null;
  const res = await fetch(`${API}${path}`, {
    ...opts,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

interface Account { id: number; name: string; phone: string; status: string; platform: string; account_identifier: string; }
interface PoolEntry { account_id: number; account_name: string | null; phone: string | null; is_healthy: boolean; sent_today: number; daily_cap_today: number; account_status: string; }
interface Campaign { id: number; name: string; status: string; platform: string; created_at: string; }
interface Schedule { id: number; start_hour: number; end_hour: number; is_active: boolean; }

// ─── Stats Bar ────────────────────────────────────────────────────────────────
function StatsBar({ pool }: { pool: PoolEntry[] }) {
  const healthy = pool.filter(p => p.is_healthy).length;
  const totalSent = pool.reduce((a, p) => a + p.sent_today, 0);
  const totalCap = pool.reduce((a, p) => a + p.daily_cap_today, 0);

  return (
    <div className="mmp-kpi-grid" style={{ marginBottom: 24 }}>
      {[
        { label: "اکانت‌های فعال", value: healthy, color: "var(--success)" },
        { label: "کل pool", value: pool.length, color: "var(--info)" },
        { label: "ارسال امروز", value: totalSent, color: "var(--primary)" },
        { label: "ظرفیت روزانه", value: totalCap, color: "var(--warning)" },
      ].map((s, i) => (
        <div key={i} className="mmp-panel" style={{ padding: "16px 20px", textAlign: "center" }}>
          <div style={{ fontSize: 28, fontWeight: 700, color: s.color }}>{s.value}</div>
          <div className="mmp-stat-card__label">{s.label}</div>
        </div>
      ))}
    </div>
  );
}

// ─── Accounts Tab ─────────────────────────────────────────────────────────────
function AccountsTab() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [loginModal, setLoginModal] = useState<{ accountId: number; phone: string; step: "phone" | "code"; regToken: string } | null>(null);
  const [phoneInput, setPhoneInput] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const [accRes, poolRes] = await Promise.all([apiFetch("/accounts"), apiFetch("/bale/user/pool")]);
    const raw = Array.isArray(accRes.data) ? accRes.data : (accRes.data?.items || []);
    setAccounts(raw.filter((a: Account) => a.platform === "bale"));
    setPool(Array.isArray(poolRes.data) ? poolRes.data : []);
    setLoading(false);
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const getPool = (id: number) => pool.find(p => p.account_id === id);

  const openLogin = (acc: Account) => {
    setPhoneInput(acc.account_identifier || "");
    setLoginModal({ accountId: acc.id, phone: acc.account_identifier || "", step: "phone", regToken: "" });
    setError(""); setMsg("");
  };

  const sendCode = async () => {
    if (!loginModal) return;
    setError("");
    const r = await apiFetch("/bale/user/login/start", {
      method: "POST",
      body: JSON.stringify({ account_id: loginModal.accountId, phone_number: phoneInput }),
    });
    if (!r.ok) { setError(r.data.detail || "خطا در ارسال کد"); return; }
    setLoginModal({ ...loginModal, step: "code", phone: phoneInput, regToken: r.data.registration_token });
    setMsg("کد به شماره ارسال شد ✅");
  };

  const verifyCode = async () => {
    if (!loginModal) return;
    setError("");
    const r = await apiFetch("/bale/user/login/verify", {
      method: "POST",
      body: JSON.stringify({ account_id: loginModal.accountId, registration_token: loginModal.regToken, code }),
    });
    if (!r.ok) { setError(r.data.detail || "کد اشتباه است"); return; }
    setMsg(`✅ لاگین موفق — user_id: ${r.data.user_id}`);
    setLoginModal(null); setCode("");
    fetchAll();
  };

  const resetHealth = async (id: number) => {
    await apiFetch(`/bale/user/pool/${id}/reset`, { method: "POST" });
    fetchAll();
  };

  const removePool = async (id: number) => {
    if (!confirm("این اکانت از pool حذف شود؟")) return;
    await apiFetch(`/bale/user/pool/${id}/remove`, { method: "POST" });
    fetchAll();
  };

  if (loading) return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>در حال بارگذاری...</div>
  );

  return (
    <div>
      {msg && (
        <div className="mmp-panel" style={{ marginBottom: 16, padding: "12px 16px", background: "var(--success-soft)", border: "1px solid var(--success)", borderRadius: "var(--radius-sm)" }}>
          <span style={{ color: "var(--success)", fontSize: 14 }}>{msg}</span>
        </div>
      )}

      {/* Login Modal */}
      {loginModal && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)", zIndex: 999, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div className="mmp-panel" style={{ width: 400, padding: 28, borderRadius: "var(--radius-md)" }}>
            <h3 style={{ fontWeight: 700, marginBottom: 20, fontSize: 16 }}>
              {loginModal.step === "phone" ? "ورود با شماره موبایل" : "تأیید کد بله"}
            </h3>
            {error && <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12, padding: "8px 12px", background: "var(--danger-soft)", borderRadius: "var(--radius-sm)" }}>{error}</div>}
            {msg && loginModal.step === "code" && <div style={{ color: "var(--success)", fontSize: 13, marginBottom: 12 }}>{msg}</div>}

            {loginModal.step === "phone" ? (
              <>
                <label style={{ fontSize: 13, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>شماره موبایل</label>
                <input value={phoneInput} onChange={e => setPhoneInput(e.target.value)}
                  style={{ width: "100%", padding: "10px 12px", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", fontSize: 14, marginBottom: 16, boxSizing: "border-box" }}
                  placeholder="09XXXXXXXXX" />
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={sendCode} className="mmp-btn mmp-btn--primary" style={{ flex: 1 }}>ارسال کد</button>
                  <button onClick={() => setLoginModal(null)} className="mmp-btn" style={{ flex: 1 }}>انصراف</button>
                </div>
              </>
            ) : (
              <>
                <p style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 12 }}>کد ارسال شده به <strong>{loginModal.phone}</strong> را وارد کنید:</p>
                <input value={code} onChange={e => setCode(e.target.value)}
                  style={{ width: "100%", padding: "10px 12px", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", fontSize: 14, marginBottom: 16, boxSizing: "border-box", letterSpacing: 4, textAlign: "center" }}
                  placeholder="- - - - -" maxLength={6} />
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={verifyCode} className="mmp-btn mmp-btn--primary" style={{ flex: 1 }}>تأیید کد</button>
                  <button onClick={() => setLoginModal({ ...loginModal, step: "phone" })} className="mmp-btn" style={{ flex: 1 }}>بازگشت</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Account Cards */}
      {accounts.length === 0 ? (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-muted)" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>📱</div>
          <p>هیچ اکانت بله‌ای وجود ندارد.</p>
          <p style={{ fontSize: 13 }}>از بخش اکانت‌ها یک اکانت با پلتفرم bale اضافه کنید.</p>
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 16 }}>
          {accounts.map(acc => {
            const p = getPool(acc.id);
            const isInPool = !!p;
            const isHealthy = p?.is_healthy ?? false;
            const pct = p ? Math.round((p.sent_today / Math.max(p.daily_cap_today, 1)) * 100) : 0;

            return (
              <div key={acc.id} className="mmp-panel" style={{ padding: 20 }}>
                {/* Header */}
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>{acc.name || `اکانت ${acc.id}`}</div>
                    <div style={{ fontSize: 13, color: "var(--text-muted)", direction: "ltr" }}>{acc.account_identifier}</div>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-end" }}>
                    <span style={{
                      padding: "3px 10px", borderRadius: 20, fontSize: 11, fontWeight: 600,
                      background: isInPool && isHealthy ? "var(--success-soft)" : "var(--danger-soft)",
                      color: isInPool && isHealthy ? "var(--success)" : "var(--danger)",
                    }}>
                      {isInPool && isHealthy ? "✅ لاگین" : "❌ خارج شده"}
                    </span>
                    <span style={{
                      padding: "3px 10px", borderRadius: 20, fontSize: 11,
                      background: "var(--surface-muted)", color: "var(--text-muted)",
                    }}>{acc.status}</span>
                  </div>
                </div>

                {/* Progress */}
                {p && (
                  <div style={{ marginBottom: 16 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "var(--text-muted)", marginBottom: 6 }}>
                      <span>ارسال امروز</span>
                      <span><strong>{p.sent_today}</strong> / {p.daily_cap_today}</span>
                    </div>
                    <div style={{ height: 6, background: "var(--surface-muted)", borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${pct}%`, background: pct > 80 ? "var(--danger)" : "var(--primary)", borderRadius: 3, transition: "width 0.3s" }} />
                    </div>
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={() => openLogin(acc)} className="mmp-btn mmp-btn--primary" style={{ flex: 1, fontSize: 13 }}>
                    {isInPool ? "تمدید session" : "ورود"}
                  </button>
                  {p && !p.is_healthy && (
                    <button onClick={() => resetHealth(acc.id)} className="mmp-btn" style={{ fontSize: 13, color: "var(--warning)" }}>ریست</button>
                  )}
                  {p && (
                    <button onClick={() => removePool(acc.id)} className="mmp-btn" style={{ fontSize: 13, color: "var(--danger)" }}>حذف</button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Campaigns Tab ────────────────────────────────────────────────────────────
function CampaignsTab() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const fetch = useCallback(async () => {
    setLoading(true);
    const r = await apiFetch("/campaigns?limit=50");
    const all = Array.isArray(r.data) ? r.data : (r.data?.items || []);
    setCampaigns(all.filter((c: Campaign) => c.platform === "bale"));
    setLoading(false);
  }, []);

  useEffect(() => { fetch(); }, [fetch]);

  const start = async (id: number) => { await apiFetch(`/campaigns/${id}/start`, { method: "POST" }); setMsg("کمپین شروع شد"); fetch(); };
  const stop = async (id: number) => { await apiFetch(`/campaigns/${id}/stop`, { method: "POST" }); setMsg("کمپین متوقف شد"); fetch(); };

  const statusConfig: Record<string, { bg: string; color: string; label: string }> = {
    running: { bg: "var(--success-soft)", color: "var(--success)", label: "در حال اجرا" },
    completed: { bg: "var(--primary-soft)", color: "var(--primary)", label: "تکمیل شده" },
    stopped: { bg: "var(--danger-soft)", color: "var(--danger)", label: "متوقف" },
    pending: { bg: "rgba(180,83,9,0.08)", color: "var(--warning)", label: "در انتظار" },
    draft: { bg: "var(--surface-muted)", color: "var(--text-muted)", label: "پیش‌نویس" },
  };

  if (loading) return <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>در حال بارگذاری...</div>;

  return (
    <div>
      {msg && <div style={{ marginBottom: 16, padding: "12px 16px", background: "var(--success-soft)", borderRadius: "var(--radius-sm)", fontSize: 14, color: "var(--success)" }}>{msg}</div>}
      {campaigns.length === 0 ? (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-muted)" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>📋</div>
          <p>هیچ کمپین بله‌ای وجود ندارد.</p>
        </div>
      ) : (
        <div className="mmp-panel" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", borderBottom: "1px solid var(--border)" }}>
                {["نام کمپین", "وضعیت", "تاریخ", "عملیات"].map((h, i) => (
                  <th key={i} style={{ padding: "12px 16px", textAlign: i === 0 ? "right" : "center", fontWeight: 600, fontSize: 13, color: "var(--text-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c, i) => {
                const s = statusConfig[c.status] || statusConfig.draft;
                return (
                  <tr key={c.id} style={{ borderBottom: "1px solid var(--border)", background: i % 2 === 0 ? "transparent" : "var(--surface-muted)" }}>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{c.name}</td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <span style={{ padding: "4px 12px", borderRadius: 20, fontSize: 12, fontWeight: 600, background: s.bg, color: s.color }}>{s.label}</span>
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "center", fontSize: 12, color: "var(--text-muted)" }}>
                      {new Date(c.created_at).toLocaleDateString("fa-IR")}
                    </td>
                    <td style={{ padding: "12px 16px", textAlign: "center" }}>
                      <div style={{ display: "flex", gap: 8, justifyContent: "center" }}>
                        {c.status === "running"
                          ? <button onClick={() => stop(c.id)} className="mmp-btn" style={{ fontSize: 12, color: "var(--danger)", padding: "4px 12px" }}>توقف</button>
                          : <button onClick={() => start(c.id)} className="mmp-btn mmp-btn--primary" style={{ fontSize: 12, padding: "4px 12px" }}>اجرا</button>
                        }
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Contacts Tab ─────────────────────────────────────────────────────────────
function ContactsTab() {
  const [contacts, setContacts] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  const fetchContacts = useCallback(async (p = 1, q = "") => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "20", offset: String((p - 1) * 20) });
    const r = await apiFetch(`/debug/contacts/latest?${params}`);
    const data = r.data;
    setContacts(Array.isArray(data) ? data : (data?.items || []));
    setLoading(false);
  }, []);

  useEffect(() => { fetchContacts(1); }, [fetchContacts]);

  const consentConfig: Record<string, { color: string; label: string }> = {
    opted_in: { color: "var(--success)", label: "فعال" },
    opted_out: { color: "var(--danger)", label: "خارج شده" },
    unknown: { color: "var(--text-muted)", label: "نامشخص" },
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
        <input value={search} onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === "Enter" && fetchContacts(1, search)}
          style={{ flex: 1, padding: "10px 14px", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", fontSize: 14 }}
          placeholder="جستجو شماره یا نام..." />
        <button onClick={() => fetchContacts(1, search)} className="mmp-btn mmp-btn--primary">جستجو</button>
      </div>

      {loading ? <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>در حال بارگذاری...</div> : (
        <div className="mmp-panel" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", borderBottom: "1px solid var(--border)" }}>
                {["نام", "شماره موبایل", "وضعیت رضایت"].map((h, i) => (
                  <th key={i} style={{ padding: "12px 16px", textAlign: "right", fontWeight: 600, fontSize: 13, color: "var(--text-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {contacts.length === 0 ? (
                <tr><td colSpan={3} style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>مخاطبی پیدا نشد</td></tr>
              ) : contacts.map((c, i) => {
                const cs = consentConfig[c.consent_status] || consentConfig.unknown;
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)", background: i % 2 === 0 ? "transparent" : "var(--surface-muted)" }}>
                    <td style={{ padding: "12px 16px", fontWeight: 500 }}>{c.first_name || c.full_name || "—"}</td>
                    <td style={{ padding: "12px 16px", direction: "ltr", fontSize: 13 }}>{c.phone || c.phone_e164 || "—"}</td>
                    <td style={{ padding: "12px 16px" }}>
                      <span style={{ padding: "3px 10px", borderRadius: 20, fontSize: 12, color: cs.color, background: `${cs.color}15` }}>{cs.label}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ padding: "12px 16px", display: "flex", gap: 8, justifyContent: "center", borderTop: "1px solid var(--border)" }}>
            <button onClick={() => { setPage(p => Math.max(1, p-1)); fetchContacts(page-1, search); }}
              disabled={page === 1} className="mmp-btn" style={{ fontSize: 13 }}>قبلی</button>
            <span style={{ fontSize: 13, color: "var(--text-muted)", padding: "6px 12px" }}>صفحه {page}</span>
            <button onClick={() => { setPage(p => p+1); fetchContacts(page+1, search); }}
              disabled={contacts.length < 20} className="mmp-btn" style={{ fontSize: 13 }}>بعدی</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Logs Tab ─────────────────────────────────────────────────────────────────
function LogsTab() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "delivered" | "failed">("all");
  const intervalRef = useRef<NodeJS.Timeout | undefined>(undefined);

  const fetchLogs = useCallback(async () => {
    const r = await apiFetch("/campaigns?limit=5");
    const camps = Array.isArray(r.data) ? r.data : (r.data?.items || []);
    const baleCamps = camps.filter((c: Campaign) => c.platform === "bale");
    const allLogs: any[] = [];
    await Promise.all(baleCamps.slice(0, 3).map(async (c: Campaign) => {
      const r2 = await apiFetch(`/campaigns/${c.id}/recipients?limit=30`);
      const recs = Array.isArray(r2.data) ? r2.data : (r2.data?.items || []);
      recs.forEach((rec: any) => allLogs.push({ ...rec, campaign_name: c.name }));
    }));
    setLogs(allLogs);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchLogs();
    intervalRef.current = setInterval(fetchLogs, 10000);
    return () => clearInterval(intervalRef.current);
  }, [fetchLogs]);

  const filtered = logs.filter(l => {
    if (filter === "delivered") return l.status === "delivered";
    if (filter === "failed") return l.status?.includes("failed");
    return true;
  });

  const statusConfig: Record<string, { bg: string; color: string; label: string }> = {
    delivered: { bg: "var(--success-soft)", color: "var(--success)", label: "ارسال شد" },
    failed_permanent: { bg: "var(--danger-soft)", color: "var(--danger)", label: "خطای دائم" },
    failed_retryable: { bg: "rgba(180,83,9,0.08)", color: "var(--warning)", label: "در انتظار retry" },
    pending: { bg: "var(--surface-muted)", color: "var(--text-muted)", label: "در انتظار" },
    queued: { bg: "rgba(29,78,216,0.08)", color: "var(--info)", label: "در صف" },
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 8, marginBottom: 20, alignItems: "center" }}>
        {[
          { key: "all", label: "همه" },
          { key: "delivered", label: "موفق" },
          { key: "failed", label: "ناموفق" },
        ].map(f => (
          <button key={f.key} onClick={() => setFilter(f.key as any)}
            style={{
              padding: "6px 16px", borderRadius: 20, fontSize: 13, border: "1px solid var(--border)",
              background: filter === f.key ? "var(--primary)" : "var(--surface)",
              color: filter === f.key ? "white" : "var(--text-muted)",
              cursor: "pointer",
            }}>{f.label}</button>
        ))}
        <button onClick={fetchLogs} style={{ marginRight: "auto", fontSize: 12, color: "var(--info)", background: "none", border: "none", cursor: "pointer" }}>🔄 رفرش</button>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>هر ۱۰ ثانیه آپدیت</span>
      </div>

      {loading ? <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>در حال بارگذاری...</div> : (
        <div className="mmp-panel" style={{ padding: 0, overflow: "hidden" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ background: "var(--surface-muted)", borderBottom: "1px solid var(--border)" }}>
                {["کمپین", "شماره", "وضعیت", "خطا"].map((h, i) => (
                  <th key={i} style={{ padding: "12px 16px", textAlign: "right", fontWeight: 600, fontSize: 12, color: "var(--text-muted)" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={4} style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>لاگی وجود ندارد</td></tr>
              ) : filtered.map((log, i) => {
                const s = statusConfig[log.status] || { bg: "var(--surface-muted)", color: "var(--text-muted)", label: log.status };
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--border)", background: i % 2 === 0 ? "transparent" : "var(--surface-muted)" }}>
                    <td style={{ padding: "10px 16px" }}>{log.campaign_name}</td>
                    <td style={{ padding: "10px 16px", direction: "ltr" }}>{log.phone || log.recipient || "—"}</td>
                    <td style={{ padding: "10px 16px" }}>
                      <span style={{ padding: "3px 10px", borderRadius: 20, fontSize: 11, fontWeight: 600, background: s.bg, color: s.color }}>{s.label}</span>
                    </td>
                    <td style={{ padding: "10px 16px", color: "var(--danger)", fontSize: 12, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {log.failure_reason || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Schedule Tab ─────────────────────────────────────────────────────────────
function ScheduleTab() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [newStart, setNewStart] = useState("8");
  const [newEnd, setNewEnd] = useState("22");
  const [msg, setMsg] = useState("");

  const fetchSchedules = useCallback(async () => {
    const r = await apiFetch("/bale/user/schedules");
    setSchedules(Array.isArray(r.data) ? r.data : []);
  }, []);

  useEffect(() => { fetchSchedules(); }, [fetchSchedules]);

  const add = async () => {
    await apiFetch("/bale/user/schedules", { method: "POST", body: JSON.stringify({ start_hour: +newStart, end_hour: +newEnd, is_active: true }) });
    setMsg("بازه اضافه شد"); fetchSchedules();
  };

  const toggle = async (s: Schedule) => {
    await apiFetch(`/bale/user/schedules/${s.id}`, { method: "PUT", body: JSON.stringify({ ...s, is_active: !s.is_active }) });
    fetchSchedules();
  };

  const del = async (id: number) => {
    await apiFetch(`/bale/user/schedules/${id}`, { method: "DELETE" });
    setMsg("بازه حذف شد"); fetchSchedules();
  };

  return (
    <div>
      {msg && <div style={{ marginBottom: 16, padding: "12px 16px", background: "var(--success-soft)", borderRadius: "var(--radius-sm)", fontSize: 14, color: "var(--success)" }}>{msg}</div>}

      <div className="mmp-panel" style={{ padding: 20, marginBottom: 20 }}>
        <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 16 }}>افزودن بازه زمانی جدید</h3>
        <div style={{ display: "flex", gap: 16, alignItems: "flex-end" }}>
          <div>
            <label style={{ fontSize: 12, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>از ساعت</label>
            <input type="number" min="0" max="23" value={newStart} onChange={e => setNewStart(e.target.value)}
              style={{ width: 80, padding: "8px 12px", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", fontSize: 14 }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>تا ساعت</label>
            <input type="number" min="0" max="23" value={newEnd} onChange={e => setNewEnd(e.target.value)}
              style={{ width: 80, padding: "8px 12px", border: "1px solid var(--border-strong)", borderRadius: "var(--radius-sm)", fontSize: 14 }} />
          </div>
          <button onClick={add} className="mmp-btn mmp-btn--primary">افزودن</button>
        </div>
      </div>

      <div className="mmp-panel" style={{ padding: 0, overflow: "hidden" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr style={{ background: "var(--surface-muted)", borderBottom: "1px solid var(--border)" }}>
              {["از ساعت", "تا ساعت", "وضعیت", "عملیات"].map((h, i) => (
                <th key={i} style={{ padding: "12px 16px", textAlign: "right", fontWeight: 600, fontSize: 13, color: "var(--text-muted)" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {schedules.length === 0 ? (
              <tr><td colSpan={4} style={{ textAlign: "center", padding: 40, color: "var(--text-muted)" }}>بازه‌ای تعریف نشده</td></tr>
            ) : schedules.map((s, i) => (
              <tr key={s.id} style={{ borderBottom: "1px solid var(--border)", background: i % 2 === 0 ? "transparent" : "var(--surface-muted)" }}>
                <td style={{ padding: "12px 16px" }}>{s.start_hour}:00</td>
                <td style={{ padding: "12px 16px" }}>{s.end_hour}:00</td>
                <td style={{ padding: "12px 16px" }}>
                  <span style={{ padding: "3px 10px", borderRadius: 20, fontSize: 12, fontWeight: 600, background: s.is_active ? "var(--success-soft)" : "var(--surface-muted)", color: s.is_active ? "var(--success)" : "var(--text-muted)" }}>
                    {s.is_active ? "فعال" : "غیرفعال"}
                  </span>
                </td>
                <td style={{ padding: "12px 16px" }}>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button onClick={() => toggle(s)} className="mmp-btn" style={{ fontSize: 12, color: "var(--info)" }}>{s.is_active ? "غیرفعال" : "فعال"}</button>
                    <button onClick={() => del(s.id)} className="mmp-btn" style={{ fontSize: 12, color: "var(--danger)" }}>حذف</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────
export default function BalePage() {
  const [tab, setTab] = useState<"accounts" | "campaigns" | "contacts" | "logs" | "schedule">("accounts");
  const [pool, setPool] = useState<PoolEntry[]>([]);

  useEffect(() => {
    apiFetch("/bale/user/pool").then(r => setPool(Array.isArray(r.data) ? r.data : []));
  }, []);

  const tabs = [
    { key: "accounts", label: "اکانت‌ها", icon: "👤" },
    { key: "campaigns", label: "کمپین‌ها", icon: "📣" },
    { key: "contacts", label: "مخاطبین", icon: "📋" },
    { key: "logs", label: "لاگ زنده", icon: "📊" },
    { key: "schedule", label: "بازه زمانی", icon: "🕐" },
  ] as const;

  return (
    <Layout title="کنترل پنل بله">
      <div className="mmp-page">
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>بله — کنترل پنل</h1>
          <p style={{ fontSize: 13, color: "var(--text-muted)" }}>مدیریت کامل اکانت‌ها، مخاطبین، کمپین‌ها و لاگ ارسال</p>
        </div>

        <StatsBar pool={pool} />

        <div className="mmp-panel" style={{ padding: 0, overflow: "hidden" }}>
          {/* Tab Header */}
          <div style={{ display: "flex", borderBottom: "1px solid var(--border)", background: "var(--surface)" }}>
            {tabs.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                style={{
                  padding: "14px 20px", fontSize: 13, fontWeight: 500, border: "none", cursor: "pointer",
                  borderBottom: tab === t.key ? "2px solid var(--primary)" : "2px solid transparent",
                  color: tab === t.key ? "var(--primary)" : "var(--text-muted)",
                  background: "transparent",
                  display: "flex", alignItems: "center", gap: 6,
                  transition: "all 0.15s",
                }}>
                <span>{t.icon}</span> {t.label}
              </button>
            ))}
          </div>

          {/* Tab Content */}
          <div style={{ padding: 24 }}>
            {tab === "accounts" && <AccountsTab />}
            {tab === "campaigns" && <CampaignsTab />}
            {tab === "contacts" && <ContactsTab />}
            {tab === "logs" && <LogsTab />}
            {tab === "schedule" && <ScheduleTab />}
          </div>
        </div>
      </div>
    </Layout>
  );
}
