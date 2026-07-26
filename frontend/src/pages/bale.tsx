import { useState, useEffect } from "react";
import { Layout } from "@/components/Layout";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

// ─── types ───────────────────────────────────────────────────────────────────
interface PoolEntry {
  account_id: number;
  account_name: string | null;
  phone: string | null;
  is_healthy: boolean;
  sent_today: number;
  daily_cap_today: number;
  account_status: string;
}

interface Schedule {
  id: number;
  start_hour: number;
  end_hour: number;
  is_active: boolean;
}

// ─── Pool Tab ────────────────────────────────────────────────────────────────
function PoolTab() {
  const [pool, setPool] = useState<PoolEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [msg, setMsg] = useState("");

  const fetchPool = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/bale/user/pool`, { credentials: "include" });
      const data = await r.json();
      setPool(Array.isArray(data) ? data : []);
    } catch { setPool([]); }
    setLoading(false);
  };

  useEffect(() => { fetchPool(); }, []);

  const resetHealth = async (account_id: number) => {
    await fetch(`${API}/bale/user/pool/${account_id}/reset`, { method: "POST", credentials: "include" });
    setMsg("اکانت ریست شد");
    fetchPool();
  };

  const removeFromPool = async (account_id: number) => {
    if (!confirm("از pool حذف شود؟")) return;
    await fetch(`${API}/bale/user/pool/${account_id}/remove`, { method: "POST", credentials: "include" });
    setMsg("اکانت حذف شد");
    fetchPool();
  };

  if (loading) return <p className="text-sm text-gray-500">در حال بارگذاری...</p>;

  return (
    <div className="space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      {pool.length === 0 ? (
        <p className="text-sm text-gray-500">هیچ اکانتی در pool بله نیست. ابتدا لاگین کنید.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-gray-50 text-gray-600">
                <th className="border border-gray-200 px-3 py-2 text-right">اکانت</th>
                <th className="border border-gray-200 px-3 py-2 text-right">شماره</th>
                <th className="border border-gray-200 px-3 py-2 text-center">وضعیت</th>
                <th className="border border-gray-200 px-3 py-2 text-center">ارسال امروز</th>
                <th className="border border-gray-200 px-3 py-2 text-center">سقف روزانه</th>
                <th className="border border-gray-200 px-3 py-2 text-center">عملیات</th>
              </tr>
            </thead>
            <tbody>
              {pool.map((entry) => (
                <tr key={entry.account_id} className="hover:bg-gray-50">
                  <td className="border border-gray-200 px-3 py-2">{entry.account_name || entry.account_id}</td>
                  <td className="border border-gray-200 px-3 py-2">{entry.phone || "—"}</td>
                  <td className="border border-gray-200 px-3 py-2 text-center">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${entry.is_healthy ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                      {entry.is_healthy ? "سالم" : "خراب"}
                    </span>
                  </td>
                  <td className="border border-gray-200 px-3 py-2 text-center">{entry.sent_today}</td>
                  <td className="border border-gray-200 px-3 py-2 text-center">{entry.daily_cap_today}</td>
                  <td className="border border-gray-200 px-3 py-2 text-center space-x-2">
                    {!entry.is_healthy && (
                      <button onClick={() => resetHealth(entry.account_id)} className="text-blue-600 hover:underline text-xs ml-2">ریست</button>
                    )}
                    <button onClick={() => removeFromPool(entry.account_id)} className="text-red-600 hover:underline text-xs">حذف</button>
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

// ─── Login Tab ────────────────────────────────────────────────────────────────
function LoginTab() {
  const [accountId, setAccountId] = useState("");
  const [phone, setPhone] = useState("");
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [regToken, setRegToken] = useState("");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  const sendCode = async () => {
    setLoading(true); setError(""); setMsg("");
    try {
      const r = await fetch(`${API}/bale/user/login/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ account_id: parseInt(accountId), phone_number: phone }),
      });
      const data = await r.json();
      if (!r.ok) { setError(data.detail || "خطا"); setLoading(false); return; }
      setRegToken(data.registration_token);
      setStep("code");
      setMsg("کد به شماره ارسال شد.");
    } catch (e) { setError("خطای شبکه"); }
    setLoading(false);
  };

  const verifyCode = async () => {
    setLoading(true); setError(""); setMsg("");
    try {
      const r = await fetch(`${API}/bale/user/login/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ account_id: parseInt(accountId), registration_token: regToken, code }),
      });
      const data = await r.json();
      if (!r.ok) { setError(data.detail || "خطا"); setLoading(false); return; }
      setMsg(`✅ لاگین موفق! user_id: ${data.user_id}`);
      setStep("phone"); setCode(""); setPhone(""); setAccountId("");
    } catch (e) { setError("خطای شبکه"); }
    setLoading(false);
  };

  return (
    <div className="max-w-md space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      {error && <div className="bg-red-50 border border-red-200 rounded p-3 text-sm text-red-700">{error}</div>}

      {step === "phone" ? (
        <>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">شناسه اکانت</label>
            <input type="number" value={accountId} onChange={e => setAccountId(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="مثال: 5" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">شماره موبایل</label>
            <input type="text" value={phone} onChange={e => setPhone(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="مثال: 09121234567" />
          </div>
          <button onClick={sendCode} disabled={loading || !accountId || !phone}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
            {loading ? "در حال ارسال..." : "ارسال کد"}
          </button>
        </>
      ) : (
        <>
          <p className="text-sm text-gray-600">کد ارسال شده به <strong>{phone}</strong> را وارد کنید:</p>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">کد تأیید</label>
            <input type="text" value={code} onChange={e => setCode(e.target.value)}
              className="w-full border border-gray-300 rounded px-3 py-2 text-sm" placeholder="مثال: 12345" />
          </div>
          <div className="flex gap-2">
            <button onClick={verifyCode} disabled={loading || !code}
              className="bg-green-600 text-white px-4 py-2 rounded text-sm disabled:opacity-50">
              {loading ? "در حال تأیید..." : "تأیید کد"}
            </button>
            <button onClick={() => { setStep("phone"); setError(""); setMsg(""); }}
              className="bg-gray-200 text-gray-700 px-4 py-2 rounded text-sm">
              بازگشت
            </button>
          </div>
        </>
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

  const fetchSchedules = async () => {
    try {
      const r = await fetch(`${API}/bale/user/schedules`, { credentials: "include" });
      const data = await r.json();
      setSchedules(Array.isArray(data) ? data : []);
    } catch { setSchedules([]); }
  };

  useEffect(() => { fetchSchedules(); }, []);

  const addSchedule = async () => {
    await fetch(`${API}/bale/user/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ start_hour: parseInt(newStart), end_hour: parseInt(newEnd), is_active: true }),
    });
    setMsg("بازه اضافه شد"); fetchSchedules();
  };

  const toggleSchedule = async (s: Schedule) => {
    await fetch(`${API}/bale/user/schedules/${s.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ start_hour: s.start_hour, end_hour: s.end_hour, is_active: !s.is_active }),
    });
    fetchSchedules();
  };

  const deleteSchedule = async (id: number) => {
    await fetch(`${API}/bale/user/schedules/${id}`, { method: "DELETE", credentials: "include" });
    setMsg("بازه حذف شد"); fetchSchedules();
  };

  return (
    <div className="space-y-4">
      {msg && <div className="bg-green-50 border border-green-200 rounded p-3 text-sm text-green-700">{msg}</div>}
      <div className="flex items-end gap-3">
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
        <button onClick={addSchedule} className="bg-blue-600 text-white px-3 py-1 rounded text-sm">افزودن</button>
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
          {schedules.map(s => (
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
                  {s.is_active ? "غیرفعال" : "فعال"}
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
  const [tab, setTab] = useState<"pool" | "login" | "schedule">("pool");

  const tabs = [
    { key: "pool", label: "استخر اکانت‌ها" },
    { key: "login", label: "لاگین با شماره" },
    { key: "schedule", label: "بازه زمانی" },
  ] as const;

  return (
    <Layout title="مدیریت بله">
      <div className="p-6 space-y-6">
        <h1 className="text-2xl font-semibold">بله — User Account</h1>
        <p className="text-sm text-gray-500">ارسال مستقیم به شماره موبایل از طریق اکانت شخصی بله</p>

        {/* Tabs */}
        <div className="border-b border-gray-200">
          <nav className="flex gap-4">
            {tabs.map(t => (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`pb-2 text-sm font-medium border-b-2 transition-colors ${tab === t.key ? "border-blue-600 text-blue-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
                {t.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Tab Content */}
        <div className="bg-white border border-gray-200 rounded-lg p-6">
          {tab === "pool" && <PoolTab />}
          {tab === "login" && <LoginTab />}
          {tab === "schedule" && <ScheduleTab />}
        </div>
      </div>
    </Layout>
  );
}
