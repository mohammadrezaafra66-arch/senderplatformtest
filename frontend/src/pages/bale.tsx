import { useState, useEffect, useRef } from "react";
import { Layout } from "@/components/Layout";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── helpers ─────────────────────────────────────────────────────────────────
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

// ─── types ───────────────────────────────────────────────────────────────────
interface Account { id: number; name: string; phone: string; status: string; platform: string; }
interface PoolEntry { account_id: number; account_name: string | null; phone: string | null; is_healthy: boolean; sent_today: number; daily_cap_today: number; account_status: string; }
interface Campaign { id: number; name: string; status: string; platform: string; created_at: string; }
interface Schedule { id: number; start_hour: number; end_hour: number; is_active: boolean; }
interface SessionStatus { has_session: boolean; session_type: string | null; is_active: boolean; }

// ─── AccountsTab ─────────────────────────────────────────────────────────────
function AccountsTab() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [sessions, setSessions] = useState<Record<number, SessionStatus>>({});
  const [loginState, setLoginState] = useState<{ accountId: number; step: "phone" | "code"; phone: string; regToken: string } | null>(null);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const fetchAll = async () => {
    setLoading(true);
    const [accRes, poolRes] = await Promise.all([
      apiFetch("/accounts"),
      apiFetch("/bale/user/pool"),
    ]);
    const rawAccounts = Array.isArray(accRes.data) ? accRes.data : (accRes.data?.items || []);
    const allAccounts: Account[] = rawAccounts.filter((a: Account) => a.platform === "bale");
    setAccounts(allAccounts);
    setPool(Array.isArray(poolRes.data) ? poolRes.data : []);

    // session status برای هر اکانت
    const sessMap: Record<number, SessionStatus> = {};
    await Promise.all(allAccounts.map(async (acc) => {
      const r = await apiFetch(`/accounts/${acc.id}/session/status`);
      sessMap[acc.id] = r.ok ? r.data : { has_session: false, session_type: null, is_active: false };
    }));
    setSessions(sessMap);
    setLoading(false);
  };

  useEffect(() => { fetchAll(); }, []);

  const startLogin = async (accountId: number, phone: string) => {
    setError(""); setMsg("");
    const r = await apiFetch("/bale/user/login/start", {
      method: "POST",
      body: JSON.stringify({ account_id: accountId, phone_number: phone }),
    });
    if (!r.ok) { setError(r.data.detail || "خطا"); return; }
    setLoginState({ accountId, step: "code", phone, regToken: r.data.registration_token });
    setMsg("کد به شماره ارسال شد");
  };

  const verifyLogin = async () => {
    if (!loginState) return;
    setError(""); setMsg("");
    const r = await apiFetch("/bale/user/login/verify", {
      method: "POST",
      body: JSON.stringify({ account_id: loginState.accountId, registration_token: loginState.regToken, code }),
    });
    if (!r.ok) { setError(r.data.detail || "خطا"); return; }
    setMsg(`✅ لاگین موفق — user_id: ${r.data.user_id}`);
    setLoginState(null); setCode("");
    fetchAll();
  };

  const resetHealth = async (accountId: number) => {
    await apiFetch(`/bale/user/pool/${accountId}/reset`, { method: "POST" });
    fetchAll();
  };

  const removeFromPool = async (accountId: number) => {
    if (!confirm("از pool حذف شود؟")) return;
    await apiFetch(`/bale/user/pool/${accountId}/remove`, { method: "POST" });
    fetchAll();
  };

  const getPoolEntry = (id: number) => pool.find(p => p.account_id === id);
  const getSession = (id: number) => sessions[id];

  if (loading) return <p className="text-sm text-gray-500 p-4">در حال بارگذاری...</p>;

  return (
    <div className="space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      {error && <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>}

      {/* modal تأیید کد */}
      {loginState?.step === "code" && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-96 space-y-4 shadow-xl">
            <h3 className="font-semibold">تأیید کد بله</h3>
            <p className="text-sm text-gray-600">کد ارسال شده به <strong>{loginState.phone}</strong> را وارد کنید:</p>
            <input type="text" value={code} onChange={e => setCode(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="کد ۵ رقمی" />
            {error && <p className="text-sm text-red-600">{error}</p>}
            <div className="flex gap-2">
              <button onClick={verifyLogin} className="bg-green-600 text-white px-4 py-2 rounded text-sm flex-1">تأیید</button>
              <button onClick={() => { setLoginState(null); setCode(""); setError(""); }} className="bg-gray-200 text-gray-700 px-4 py-2 rounded text-sm">انصراف</button>
            </div>
          </div>
        </div>
      )}

      {accounts.length === 0 ? (
        <p className="text-sm text-gray-500">هیچ اکانت بله‌ای وجود ندارد. ابتدا از بخش اکانت‌ها اضافه کنید.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {accounts.map(acc => {
            const poolEntry = getPoolEntry(acc.id);
            const session = getSession(acc.id);
            const isLoggedIn = session?.has_session && session?.is_active;
            return (
              <div key={acc.id} className="border border-gray-200 rounded-lg p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="font-medium text-gray-900">{acc.name}</p>
                    <p className="text-sm text-gray-500">{acc.phone || "—"}</p>
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${isLoggedIn ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                      {isLoggedIn ? "✅ لاگین" : "❌ خارج شده"}
                    </span>
                    <span className={`px-2 py-1 rounded text-xs ${acc.status === "active" ? "bg-blue-100 text-blue-700" : "bg-gray-100 text-gray-500"}`}>
                      {acc.status}
                    </span>
                  </div>
                </div>

                {poolEntry && (
                  <div className="bg-gray-50 rounded p-2 text-xs text-gray-600 flex justify-between">
                    <span>ارسال امروز: <strong>{poolEntry.sent_today}</strong>/{poolEntry.daily_cap_today}</span>
                    <span className={poolEntry.is_healthy ? "text-green-600" : "text-red-600"}>
                      {poolEntry.is_healthy ? "سالم" : "ناسالم"}
                    </span>
                  </div>
                )}

                <div className="flex gap-2 flex-wrap">
                  {!isLoggedIn ? (
                    <button
                      onClick={() => {
                        const phone = prompt("شماره موبایل بله این اکانت:");
                        if (phone) startLogin(acc.id, phone);
                      }}
                      className="bg-blue-600 text-white px-3 py-1 rounded text-xs">
                      ورود
                    </button>
                  ) : (
                    <span className="text-xs text-green-600 font-medium">آماده ارسال</span>
                  )}
                  {poolEntry && !poolEntry.is_healthy && (
                    <button onClick={() => resetHealth(acc.id)} className="bg-yellow-500 text-white px-3 py-1 rounded text-xs">ریست</button>
                  )}
                  {poolEntry && (
                    <button onClick={() => removeFromPool(acc.id)} className="bg-red-100 text-red-600 px-3 py-1 rounded text-xs">حذف از pool</button>
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

// ─── CampaignsTab ─────────────────────────────────────────────────────────────
function CampaignsTab() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const fetchCampaigns = async () => {
    setLoading(true);
    const r = await apiFetch("/campaigns");
    const all = Array.isArray(r.data) ? r.data : (r.data?.items || []);
    setCampaigns(all.filter((c: Campaign) => c.platform === "bale"));
    setLoading(false);
  };

  useEffect(() => { fetchCampaigns(); }, []);

  const startCampaign = async (id: number) => {
    await apiFetch(`/campaigns/${id}/start`, { method: "POST" });
    setMsg("کمپین شروع شد"); fetchCampaigns();
  };

  const stopCampaign = async (id: number) => {
    await apiFetch(`/campaigns/${id}/stop`, { method: "POST" });
    setMsg("کمپین متوقف شد"); fetchCampaigns();
  };

  const statusColor: Record<string, string> = {
    running: "bg-green-100 text-green-700",
    completed: "bg-blue-100 text-blue-700",
    stopped: "bg-red-100 text-red-700",
    pending: "bg-yellow-100 text-yellow-700",
    draft: "bg-gray-100 text-gray-600",
  };

  if (loading) return <p className="text-sm text-gray-500">در حال بارگذاری...</p>;

  return (
    <div className="space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      {campaigns.length === 0 ? (
        <p className="text-sm text-gray-500">هیچ کمپین بله‌ای وجود ندارد.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-600">
                <th className="border border-gray-200 px-3 py-2 text-right">نام کمپین</th>
                <th className="border border-gray-200 px-3 py-2 text-center">وضعیت</th>
                <th className="border border-gray-200 px-3 py-2 text-center">تاریخ</th>
                <th className="border border-gray-200 px-3 py-2 text-center">عملیات</th>
              </tr>
            </thead>
            <tbody>
              {campaigns.map(c => (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="border border-gray-200 px-3 py-2">{c.name}</td>
                  <td className="border border-gray-200 px-3 py-2 text-center">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${statusColor[c.status] || "bg-gray-100 text-gray-600"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="border border-gray-200 px-3 py-2 text-center text-xs text-gray-500">
                    {new Date(c.created_at).toLocaleDateString("fa-IR")}
                  </td>
                  <td className="border border-gray-200 px-3 py-2 text-center space-x-2">
                    {c.status === "running" ? (
                      <button onClick={() => stopCampaign(c.id)} className="bg-red-100 text-red-600 px-3 py-1 rounded text-xs ml-2">توقف</button>
                    ) : (
                      <button onClick={() => startCampaign(c.id)} className="bg-green-600 text-white px-3 py-1 rounded text-xs ml-2">اجرا</button>
                    )}
                    <a href={`/campaigns/${c.id}`} className="text-blue-600 hover:underline text-xs">جزئیات</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── LogTab ───────────────────────────────────────────────────────────────────
function LogTab() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "success" | "failed">("all");
  const intervalRef = useRef<NodeJS.Timeout>();

  const fetchLogs = async () => {
    const r = await apiFetch("/dashboard/campaigns/0/stats").catch(() => ({ ok: false, data: {} }));
    // از campaigns recipients بگیریم
    const r2 = await apiFetch("/campaigns?platform=bale&limit=5");
    const camps = Array.isArray(r2.data) ? r2.data : (r2.data?.items || []);
    
    const allLogs: any[] = [];
    await Promise.all(camps.slice(0, 3).map(async (c: Campaign) => {
      const r3 = await apiFetch(`/campaigns/${c.id}/recipients?limit=20`);
      const recipients = Array.isArray(r3.data) ? r3.data : (r3.data?.items || []);
      recipients.forEach((rec: any) => allLogs.push({ ...rec, campaign_name: c.name }));
    }));
    
    setLogs(allLogs);
    setLoading(false);
  };

  useEffect(() => {
    fetchLogs();
    intervalRef.current = setInterval(fetchLogs, 10000);
    return () => clearInterval(intervalRef.current);
  }, []);

  const filtered = logs.filter(l => {
    if (filter === "success") return l.status === "delivered";
    if (filter === "failed") return l.status?.includes("failed");
    return true;
  });

  const statusColor: Record<string, string> = {
    delivered: "bg-green-100 text-green-700",
    failed_permanent: "bg-red-100 text-red-700",
    failed_retryable: "bg-yellow-100 text-yellow-700",
    pending: "bg-gray-100 text-gray-500",
    queued: "bg-blue-100 text-blue-700",
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-gray-600">فیلتر:</span>
        {(["all", "success", "failed"] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded text-xs ${filter === f ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600"}`}>
            {f === "all" ? "همه" : f === "success" ? "موفق" : "ناموفق"}
          </button>
        ))}
        <button onClick={fetchLogs} className="mr-auto text-xs text-blue-600 hover:underline">رفرش</button>
        <span className="text-xs text-gray-400">هر ۱۰ ثانیه آپدیت</span>
      </div>

      {loading ? <p className="text-sm text-gray-500">در حال بارگذاری...</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-600">
                <th className="border border-gray-200 px-3 py-2 text-right">کمپین</th>
                <th className="border border-gray-200 px-3 py-2 text-right">شماره</th>
                <th className="border border-gray-200 px-3 py-2 text-center">وضعیت</th>
                <th className="border border-gray-200 px-3 py-2 text-right">خطا</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={4} className="text-center py-4 text-gray-400 text-sm">لاگی وجود ندارد</td></tr>
              ) : filtered.map((log, i) => (
                <tr key={i} className="hover:bg-gray-50">
                  <td className="border border-gray-200 px-3 py-2 text-xs">{log.campaign_name}</td>
                  <td className="border border-gray-200 px-3 py-2 text-xs">{log.phone || log.recipient || "—"}</td>
                  <td className="border border-gray-200 px-3 py-2 text-center">
                    <span className={`px-2 py-1 rounded text-xs ${statusColor[log.status] || "bg-gray-100 text-gray-500"}`}>
                      {log.status || "—"}
                    </span>
                  </td>
                  <td className="border border-gray-200 px-3 py-2 text-xs text-red-600 max-w-xs truncate">
                    {log.failure_reason || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── ContactsTab ──────────────────────────────────────────────────────────────
function ContactsTab() {
  const [contacts, setContacts] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);

  const fetchContacts = async (p = 1, q = "") => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "20", offset: String((p - 1) * 20) });
    if (q) params.set("q", q);
    const r = await apiFetch(`/debug/contacts/latest?${params}`);
    const data = r.data;
    setContacts(Array.isArray(data) ? data : (data?.items || []));
    setTotal(data?.total || 0);
    setLoading(false);
  };

  useEffect(() => { fetchContacts(1); }, []);

  const handleSearch = () => { setPage(1); fetchContacts(1, search); };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <input type="text" value={search} onChange={e => setSearch(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleSearch()}
          className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm" placeholder="جستجو شماره یا نام..." />
        <button onClick={handleSearch} className="bg-blue-600 text-white px-4 py-2 rounded text-sm">جستجو</button>
      </div>

      {loading ? <p className="text-sm text-gray-500">در حال بارگذاری...</p> : (
        <>
          <p className="text-xs text-gray-500">مجموع: {total} مخاطب</p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="bg-gray-50 text-gray-600">
                  <th className="border border-gray-200 px-3 py-2 text-right">نام</th>
                  <th className="border border-gray-200 px-3 py-2 text-right">شماره</th>
                  <th className="border border-gray-200 px-3 py-2 text-center">وضعیت</th>
                </tr>
              </thead>
              <tbody>
                {contacts.length === 0 ? (
                  <tr><td colSpan={3} className="text-center py-4 text-gray-400 text-sm">مخاطبی وجود ندارد</td></tr>
                ) : contacts.map((c, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="border border-gray-200 px-3 py-2">{c.first_name || c.full_name || "—"}</td>
                    <td className="border border-gray-200 px-3 py-2">{c.phone || c.phone_e164 || "—"}</td>
                    <td className="border border-gray-200 px-3 py-2 text-center">
                      <span className={`px-2 py-1 rounded text-xs ${c.consent_status === "opted_in" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                        {c.consent_status || "—"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-2 justify-center">
            <button onClick={() => { setPage(p => Math.max(1, p-1)); fetchContacts(page-1, search); }}
              disabled={page === 1} className="px-3 py-1 rounded text-xs bg-gray-100 disabled:opacity-40">قبلی</button>
            <span className="text-xs text-gray-500 py-1">صفحه {page}</span>
            <button onClick={() => { setPage(p => p+1); fetchContacts(page+1, search); }}
              disabled={contacts.length < 20} className="px-3 py-1 rounded text-xs bg-gray-100 disabled:opacity-40">بعدی</button>
          </div>
        </>
      )}
    </div>
  );
}

// ─── ScheduleTab ──────────────────────────────────────────────────────────────
function ScheduleTab() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [newStart, setNewStart] = useState("8");
  const [newEnd, setNewEnd] = useState("22");
  const [msg, setMsg] = useState("");

  const fetchSchedules = async () => {
    const r = await apiFetch("/bale/user/schedules");
    setSchedules(Array.isArray(r.data) ? r.data : []);
  };

  useEffect(() => { fetchSchedules(); }, []);

  const addSchedule = async () => {
    await apiFetch("/bale/user/schedules", {
      method: "POST",
      body: JSON.stringify({ start_hour: parseInt(newStart), end_hour: parseInt(newEnd), is_active: true }),
    });
    setMsg("بازه اضافه شد"); fetchSchedules();
  };

  const toggleSchedule = async (s: Schedule) => {
    await apiFetch(`/bale/user/schedules/${s.id}`, {
      method: "PUT",
      body: JSON.stringify({ start_hour: s.start_hour, end_hour: s.end_hour, is_active: !s.is_active }),
    });
    fetchSchedules();
  };

  const deleteSchedule = async (id: number) => {
    await apiFetch(`/bale/user/schedules/${id}`, { method: "DELETE" });
    setMsg("بازه حذف شد"); fetchSchedules();
  };

  return (
    <div className="space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      <div className="flex items-end gap-3 bg-gray-50 p-3 rounded">
        <div>
          <label className="block text-xs text-gray-600 mb-1">از ساعت</label>
          <input type="number" min="0" max="23" value={newStart} onChange={e => setNewStart(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm w-16" />
        </div>
        <div>
          <label className="block text-xs text-gray-600 mb-1">تا ساعت</label>
          <input type="number" min="0" max="23" value={newEnd} onChange={e => setNewEnd(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-sm w-16" />
        </div>
        <button onClick={addSchedule} className="bg-blue-600 text-white px-4 py-2 rounded text-sm">افزودن بازه</button>
      </div>

      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="bg-gray-50 text-gray-600">
            <th className="border border-gray-200 px-3 py-2 text-right">از ساعت</th>
            <th className="border border-gray-200 px-3 py-2 text-right">تا ساعت</th>
            <th className="border border-gray-200 px-3 py-2 text-center">وضعیت</th>
            <th className="border border-gray-200 px-3 py-2 text-center">عملیات</th>
          </tr>
        </thead>
        <tbody>
          {schedules.length === 0 ? (
            <tr><td colSpan={4} className="text-center py-4 text-gray-400 text-sm">بازه‌ای تعریف نشده</td></tr>
          ) : schedules.map(s => (
            <tr key={s.id} className="hover:bg-gray-50">
              <td className="border border-gray-200 px-3 py-2">{s.start_hour}:00</td>
              <td className="border border-gray-200 px-3 py-2">{s.end_hour}:00</td>
              <td className="border border-gray-200 px-3 py-2 text-center">
                <span className={`px-2 py-1 rounded text-xs ${s.is_active ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                  {s.is_active ? "فعال" : "غیرفعال"}
                </span>
              </td>
              <td className="border border-gray-200 px-3 py-2 text-center space-x-2">
                <button onClick={() => toggleSchedule(s)} className="text-blue-600 hover:underline text-xs ml-2">
                  {s.is_active ? "غیرفعال کن" : "فعال کن"}
                </button>
                <button onClick={() => deleteSchedule(s.id)} className="text-red-600 hover:underline text-xs">حذف</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function BalePage() {
  const [tab, setTab] = useState<"accounts" | "campaigns" | "contacts" | "logs" | "schedule">("accounts");

  const tabs = [
    { key: "accounts", label: "اکانت‌ها" },
    { key: "campaigns", label: "کمپین‌ها" },
    { key: "contacts", label: "مخاطبین" },
    { key: "logs", label: "لاگ زنده" },
    { key: "schedule", label: "بازه زمانی" },
  ] as const;

  return (
    <Layout title="کنترل پنل بله">
      <div className="p-6 space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">بله — کنترل پنل</h1>
            <p className="text-sm text-gray-500 mt-1">مدیریت کامل اکانت‌ها، مخاطبین، کمپین‌ها و لاگ ارسال</p>
          </div>
        </div>

        {/* Tabs */}
        <div className="border-b border-gray-200">
          <nav className="flex gap-1">
            {tabs.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                  tab === t.key
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
                }`}>
                {t.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Content */}
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          {tab === "accounts" && <AccountsTab />}
          {tab === "campaigns" && <CampaignsTab />}
          {tab === "contacts" && <ContactsTab />}
          {tab === "logs" && <LogTab />}
          {tab === "schedule" && <ScheduleTab />}
        </div>
      </div>
    </Layout>
  );
}
