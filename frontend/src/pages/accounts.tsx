import Head from "next/head";
import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { ApiTokenSessionPanel } from "@/components/ApiTokenSessionPanel";
import { RubikaUserAccountLoginPanel } from "@/components/RubikaUserAccountLoginPanel";
import { WhatsAppWebPanel } from "@/components/WhatsAppWebPanel";
import WhatsAppEvolutionPanel from "@/components/WhatsAppEvolutionPanel";
import ProxyAssignmentForm from "@/components/ProxyAssignmentForm";
import TooltipHint from "@/components/TooltipHint";
import { Button, PageContent, TableWrap } from "@/components/ui";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ApiError } from "@/lib/api";
import {
  archiveAccount,
  assignAccountProxy,
  createAccount,
  fetchAccounts,
  restoreAccount,
  testAccountConnection,
  updateAccount,
} from "@/lib/accounts-api";
import { useAuth } from "@/state/auth";
import type {
  AccountItem,
  AccountStatusOption,
  PlatformOption,
  ProxyAssignRequest,
} from "@/types/account";
import {
  ACCOUNT_STATUS_OPTIONS,
  accountStatusColor,
  accountStatusLabel,
  PLATFORM_OPTIONS,
  runtimeStatusColor,
  runtimeStatusIcon,
  runtimeStatusLabel,
} from "@/utils/account-status";
import { toJalaliDateTime } from "@/utils/jalali";
import { canArchiveAccounts, canManageAccounts, canViewAccounts } from "@/utils/permissions";

const EVOLUTION_MODE =
  process.env.NEXT_PUBLIC_WHATSAPP_DELIVERY_MODE === "evolution";

const panelStyle: React.CSSProperties = {
  marginTop: 16,
  border: "1px solid rgba(0,0,0,0.12)",
  borderRadius: 12,
};

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid rgba(0,0,0,0.2)",
  width: "100%",
};

type CreateFormState = {
  platform: PlatformOption;
  account_identifier: string;
  label: string;
  proxy_url: string;
  status: AccountStatusOption;
};

type EditFormState = {
  account_identifier: string;
  label: string;
  proxy_url: string;
  status: AccountStatusOption;
};

const defaultCreateForm = (): CreateFormState => ({
  platform: "bale",
  account_identifier: "",
  label: "",
  proxy_url: "",
  status: "active",
});

function toEditForm(account: AccountItem): EditFormState {
  return {
    account_identifier: account.account_identifier ?? "",
    label: account.label ?? "",
    proxy_url: account.proxy_url ?? "",
    status: account.status,
  };
}

function isApiTokenPlatform(platform: PlatformOption): platform is "bale" | "telegram" | "rubika" {
  return platform === "bale" || platform === "telegram" || platform === "rubika";
}

export default function AccountsPage() {
  const { t } = useTranslation();
  const { role } = useAuth();
  const canManage = canManageAccounts(role);
  const canView = canViewAccounts(role);
  const canArchive = canArchiveAccounts(role);

  const [platformFilter, setPlatformFilter] = useState<PlatformOption | "">("");
  const [archiveView, setArchiveView] = useState<"active" | "archived">("active");
  const [items, setItems] = useState<AccountItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [archiveConfirmId, setArchiveConfirmId] = useState<number | null>(null);
  const [archiveLoading, setArchiveLoading] = useState(false);
  const [restoreLoadingId, setRestoreLoadingId] = useState<number | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(defaultCreateForm);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditFormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [waPanelId, setWaPanelId] = useState<number | null>(null);
  const [sessionPanelId, setSessionPanelId] = useState<number | null>(null);
  const [rubikaUserPanelId, setRubikaUserPanelId] = useState<number | null>(null);

  const loadAccounts = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAccounts({
        platform: platformFilter || undefined,
        archived: archiveView === "archived",
      });
      setItems(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("accountsLoadError"));
    } finally {
      setLoading(false);
    }
  }, [canView, platformFilter, archiveView, t]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

  // L18 bounded soft refresh so OTP/worker transitions appear without full page reload.
  useEffect(() => {
    if (!canView) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void loadAccounts();
      }
    }, 45000);
    return () => window.clearInterval(id);
  }, [canView, loadAccounts]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!canManage) return;
    if (!createForm.account_identifier.trim()) {
      setError(t("requiredFields"));
      return;
    }

    setCreating(true);
    setError(null);
    setNotice(null);
    try {
      const result = await createAccount({
        platform: createForm.platform,
        account_identifier: createForm.account_identifier.trim(),
        label: createForm.label.trim() || null,
        proxy_url: createForm.proxy_url.trim() || null,
        status: createForm.platform === "rubika" ? "requires_login" : createForm.status,
      });
      setNotice(result.message);
      setShowCreate(false);
      setCreateForm(defaultCreateForm());
      await loadAccounts();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setCreating(false);
    }
  }

  function startEdit(account: AccountItem) {
    setEditingId(account.id);
    setEditForm(toEditForm(account));
    setNotice(null);
    setError(null);
  }

  async function handleSaveEdit(e: React.FormEvent) {
    e.preventDefault();
    if (!canManage || editingId == null || !editForm) return;
    if (!editForm.account_identifier.trim()) {
      setError(t("requiredFields"));
      return;
    }

    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await updateAccount(editingId, {
        account_identifier: editForm.account_identifier.trim(),
        label: editForm.label.trim() || null,
        proxy_url: editForm.proxy_url.trim() || null,
        status: editForm.status,
      });
      setNotice(t("accountSaved"));
      setEditingId(null);
      setEditForm(null);
      await loadAccounts();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setSaving(false);
    }
  }

  async function handleAssignProxy(accountId: number, proxyData: ProxyAssignRequest) {
    if (!canManage) return;
    setError(null);
    setNotice(null);
    try {
      await assignAccountProxy(accountId, proxyData);
      setNotice("پروکسی با موفقیت تخصیص یافت");
    } catch (err) {
      setError("خطا در تخصیص پروکسی");
      throw err;
    } finally {
      await loadAccounts();
    }
  }

  async function handleTestConnection(accountId: number) {
    if (!canManage) return;
    setTestingId(accountId);
    setError(null);
    setNotice(null);
    try {
      const result = await testAccountConnection(accountId);
      const label = result.runtime_status_label
        ? ` — ${result.runtime_status_label}`
        : result.reason_code
          ? ` (${result.reason_code})`
          : "";
      setNotice(
        result.success
          ? `${t("testConnectionSuccess")}: ${result.message}${label}`
          : `${t("testConnectionFailed")}: ${result.error ?? result.message}${label}`,
      );
      await loadAccounts();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setTestingId(null);
    }
  }

  async function handleArchiveConfirmed() {
    if (!canArchive || archiveConfirmId == null || archiveLoading) return;
    setArchiveLoading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await archiveAccount(archiveConfirmId);
      setItems((prev) => prev.filter((item) => item.id !== archiveConfirmId));
      setNotice(result.message || "اکانت با موفقیت آرشیو شد.");
      setArchiveConfirmId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setArchiveLoading(false);
    }
  }

  async function handleRestore(accountId: number) {
    if (!canArchive || restoreLoadingId != null) return;
    setRestoreLoadingId(accountId);
    setError(null);
    setNotice(null);
    try {
      const result = await restoreAccount(accountId);
      setItems((prev) => prev.filter((item) => item.id !== accountId));
      setNotice(result.message || "اکانت از آرشیو بازیابی شد.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setRestoreLoadingId(null);
    }
  }

  const pillStyle = (active: boolean): React.CSSProperties => ({
    padding: "8px 10px",
    borderRadius: 999,
    border: "1px solid rgba(0,0,0,0.12)",
    background: active ? "rgba(0,0,0,0.08)" : "rgba(0,0,0,0.02)",
    cursor: "pointer",
  });

  return (
    <>
      <Head>
        <title>{t("accounts")}</title>
      </Head>
      <Layout title={t("accounts")}>
        <PageContent>
          {!canView ? (
            <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.14)" }}>
              {t("notAllowed")}
            </div>
          ) : (
            <>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <button
                  type="button"
                  onClick={() => setPlatformFilter("")}
                  style={{
                    padding: "8px 10px",
                    borderRadius: 999,
                    border: "1px solid rgba(0,0,0,0.12)",
                    background: !platformFilter ? "rgba(0,0,0,0.08)" : "rgba(0,0,0,0.02)",
                    cursor: "pointer",
                  }}
                >
                  {t("allPlatforms")}
                </button>
                {PLATFORM_OPTIONS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPlatformFilter(p)}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 999,
                      border: "1px solid rgba(0,0,0,0.12)",
                      background: platformFilter === p ? "rgba(0,0,0,0.08)" : "rgba(0,0,0,0.02)",
                      cursor: "pointer",
                    }}
                  >
                    {t(p)}
                  </button>
                ))}
              </div>

              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 12 }}>
                <button
                  type="button"
                  onClick={() => setArchiveView("active")}
                  style={pillStyle(archiveView === "active")}
                >
                  فعال
                </button>
                <button
                  type="button"
                  onClick={() => setArchiveView("archived")}
                  style={pillStyle(archiveView === "archived")}
                >
                  آرشیو
                </button>
              </div>

              {archiveView === "active" && canManage ? (
              <div style={{ marginTop: 16, display: "flex", gap: 10, flexWrap: "wrap" }}>
                <button
                  type="button"
                  onClick={() => {
                    setShowCreate((v) => !v);
                    setEditingId(null);
                    setEditForm(null);
                  }}
                  style={{
                    padding: "10px 12px",
                    borderRadius: 10,
                    border: "1px solid rgba(0,0,0,0.2)",
                    background: "rgba(0,0,0,0.04)",
                    cursor: "pointer",
                  }}
                >
                  {showCreate ? t("cancel") : t("addAccount")}
                </button>
                <TooltipHint text="یک اکانت واتساپ، تلگرام، بله یا روبیکا جدید به سیستم اضافه کنید" />
              </div>
              ) : null}

              {archiveView === "active" && canManage && showCreate ? (
                <form
                  onSubmit={(e) => void handleCreate(e)}
                  style={{
                    ...panelStyle,
                    padding: 12,
                    display: "grid",
                    gap: 10,
                    gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                  }}
                >
                  <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                    <span>{t("platform")}</span>
                    <select
                      value={createForm.platform}
                      onChange={(e) => {
                        const platform = e.target.value as PlatformOption;
                        setCreateForm((f) => ({
                          ...f,
                          platform,
                          status: platform === "rubika" ? "requires_login" : f.status,
                        }));
                      }}
                      style={inputStyle}
                    >
                      {PLATFORM_OPTIONS.map((p) => (
                        <option key={p} value={p}>
                          {t(p)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                    <span>{t("accountIdentifier")}</span>
                    <input
                      value={createForm.account_identifier}
                      onChange={(e) =>
                        setCreateForm((f) => ({ ...f, account_identifier: e.target.value }))
                      }
                      required
                      style={inputStyle}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                    <span>{t("label")}</span>
                    <input
                      value={createForm.label}
                      onChange={(e) => setCreateForm((f) => ({ ...f, label: e.target.value }))}
                      style={inputStyle}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                    <span>{t("proxyUrl")}</span>
                    <input
                      value={createForm.proxy_url}
                      onChange={(e) => setCreateForm((f) => ({ ...f, proxy_url: e.target.value }))}
                      style={inputStyle}
                    />
                  </label>
                  <label style={{ display: "grid", gap: 4, fontSize: 14 }}>
                    <span>{t("status")}</span>
                    <select
                      value={createForm.platform === "rubika" ? "requires_login" : createForm.status}
                      disabled={createForm.platform === "rubika"}
                      onChange={(e) =>
                        setCreateForm((f) => ({
                          ...f,
                          status: e.target.value as AccountStatusOption,
                        }))
                      }
                      style={inputStyle}
                    >
                      {ACCOUNT_STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {accountStatusLabel(s, t)}
                        </option>
                      ))}
                    </select>
                    {createForm.platform === "rubika" ? (
                      <span style={{ fontSize: 12, opacity: 0.75 }}>{t("rubikaUserLoginCreateHint")}</span>
                    ) : null}
                  </label>
                  <div style={{ display: "flex", alignItems: "end" }}>
                    <button
                      type="submit"
                      disabled={creating}
                      style={{
                        padding: "10px 14px",
                        borderRadius: 10,
                        border: "1px solid rgba(0,0,0,0.2)",
                        cursor: creating ? "wait" : "pointer",
                      }}
                    >
                      {creating ? t("loading") : t("addAccount")}
                    </button>
                  </div>
                </form>
              ) : null}

              {error ? (
                <div role="alert" style={{ marginTop: 12, color: "#991b1b", fontSize: 14 }}>
                  {error}
                </div>
              ) : null}
              {notice ? (
                <div style={{ marginTop: 12, color: "#166534", fontSize: 14 }}>{notice}</div>
              ) : null}

              <div style={panelStyle}>
                <div
                  style={{
                    padding: "10px 12px",
                    background: "rgba(0,0,0,0.02)",
                    borderBottom: "1px solid rgba(0,0,0,0.08)",
                    fontWeight: 700,
                  }}
                >
                  {t("accounts")} ({items.length})
                </div>
                {loading ? (
                  <div style={{ padding: 12 }}>{t("loading")}</div>
                ) : items.length === 0 ? (
                  <div style={{ padding: 12, opacity: 0.75 }}>
                    {archiveView === "archived" ? "اکانت آرشیوشده‌ای وجود ندارد." : t("noAccounts")}
                  </div>
                ) : archiveView === "archived" ? (
                  <TableWrap>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14, minWidth: 720 }}>
                    <thead>
                      <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("accountIdentifier")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("platform")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>تاریخ آرشیو</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("connectionStatus")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("actions")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((account) => {
                        const runtimeStatus =
                          account.runtime_status ?? account.runtime?.runtime_status ?? null;
                        const runtimeLabel = runtimeStatusLabel(
                          runtimeStatus,
                          t,
                          account.runtime_status_label ?? account.runtime?.runtime_status_label,
                          account.runtime?.reason_code,
                        );
                        const identity =
                          account.display_identity ??
                          account.label ??
                          account.account_identifier ??
                          `#${account.id}`;
                        return (
                          <tr key={account.id} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                            <td style={{ padding: 10 }}>{identity}</td>
                            <td style={{ padding: 10 }}>{t(account.platform)}</td>
                            <td style={{ padding: 10, fontSize: 13 }}>
                              {account.archived_at ? toJalaliDateTime(account.archived_at) : "—"}
                            </td>
                            <td
                              style={{
                                padding: 10,
                                color: runtimeStatusColor(runtimeStatus),
                                fontWeight: 600,
                              }}
                            >
                              {runtimeStatusIcon(runtimeStatus)} {runtimeLabel}
                            </td>
                            <td style={{ padding: 10 }}>
                              <Button
                                type="button"
                                size="sm"
                                variant="primary"
                                disabled={restoreLoadingId === account.id}
                                onClick={() => void handleRestore(account.id)}
                              >
                                {restoreLoadingId === account.id ? t("loading") : "بازیابی"}
                              </Button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  </TableWrap>
                ) : (
                  <TableWrap>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14, minWidth: 1080 }}>
                    <thead>
                      <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("accountIdCol")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("platform")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("label")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("accountIdentifier")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("accountLifecycleStatus")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("connectionStatus")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("dispatchReadiness")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("lastVerifiedAt")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("actions")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((account) => {
                        const runtimeStatus =
                          account.runtime_status ?? account.runtime?.runtime_status ?? null;
                        const runtimeLabel = runtimeStatusLabel(
                          runtimeStatus,
                          t,
                          account.runtime_status_label ?? account.runtime?.runtime_status_label,
                          account.runtime?.reason_code,
                        );
                        const dispatchReady =
                          account.runtime?.dispatch?.ready === true || runtimeStatus === "READY";
                        const verifiedAt = account.runtime?.last_verified_at ?? null;
                        return (
                        <Fragment key={account.id}>
                          <tr style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                            <td style={{ padding: 10 }}>{account.id}</td>
                            <td style={{ padding: 10 }}>{t(account.platform)}</td>
                            <td style={{ padding: 10 }}>{account.label ?? "—"}</td>
                            <td style={{ padding: 10 }}>{account.account_identifier ?? "—"}</td>
                            <td style={{ padding: 10, color: accountStatusColor(account.status) }}>
                              {account.account_enabled === false ||
                              account.status === "resting" ||
                              account.status === "banned"
                                ? t("accountEnabledOff")
                                : account.status === "active"
                                  ? t("accountEnabledOn")
                                  : accountStatusLabel(account.status, t)}
                            </td>
                            <td
                              style={{
                                padding: 10,
                                color: runtimeStatusColor(runtimeStatus),
                                fontWeight: 600,
                              }}
                              title={account.runtime?.reason_code ?? undefined}
                            >
                              {runtimeStatusIcon(runtimeStatus)} {runtimeLabel}
                            </td>
                            <td style={{ padding: 10, fontSize: 13 }}>
                              {dispatchReady
                                ? t("dispatchReadyYes")
                                : account.runtime?.dispatch?.blocker
                                  ? `${t("dispatchReadyNo")} (${account.runtime.dispatch.blocker})`
                                  : t("dispatchReadyNo")}
                            </td>
                            <td style={{ padding: 10, fontSize: 13 }}>
                              {verifiedAt ? toJalaliDateTime(verifiedAt) : "—"}
                            </td>
                            <td style={{ padding: 10, minWidth: 120 }}>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                                {canArchive ? (
                                  <Button
                                    type="button"
                                    variant="primary"
                                    size="sm"
                                    disabled={archiveLoading}
                                    onClick={() => setArchiveConfirmId(account.id)}
                                  >
                                    آرشیو
                                  </Button>
                                ) : null}
                                {canManage ? (
                                  <>
                                <button
                                  type="button"
                                  onClick={() => startEdit(account)}
                                  style={{ padding: "6px 10px", borderRadius: 8 }}
                                >
                                  {t("editAccount")}
                                </button>
                                <TooltipHint text="ویرایش اطلاعات پایه این اکانت مانند برچسب و آدرس Proxy" />
                                <button
                                  type="button"
                                  disabled={testingId === account.id}
                                  onClick={() => void handleTestConnection(account.id)}
                                  style={{ padding: "6px 10px", borderRadius: 8 }}
                                >
                                  {testingId === account.id ? t("loading") : t("testConnection")}
                                </button>
                                <TooltipHint text="بررسی امن احراز هویت/سشن بدون ارسال پیام و بدون درخواست OTP" />
                                {account.platform === "whatsapp" ? (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setSessionPanelId(null);
                                        setWaPanelId((current) =>
                                          current === account.id ? null : account.id,
                                        );
                                      }}
                                      style={{
                                        padding: "6px 10px",
                                        borderRadius: 8,
                                        background:
                                          waPanelId === account.id
                                            ? "rgba(29,78,216,0.12)"
                                            : undefined,
                                      }}
                                    >
                                      {EVOLUTION_MODE ? "اتصال Evolution" : t("waWebConnect")}
                                    </button>
                                    <TooltipHint
                                      text={
                                        EVOLUTION_MODE
                                          ? "مشاهده وضعیت اتصال این اکانت واتساپ از طریق Evolution API و مدیریت Proxy آن"
                                          : "مشاهده وضعیت اتصال این اکانت واتساپ و دریافت QR Code برای ورود"
                                      }
                                    />
                                  </>
                                ) : null}
                                {isApiTokenPlatform(account.platform) ? (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setWaPanelId(null);
                                      setSessionPanelId((current) =>
                                        current === account.id ? null : account.id,
                                      );
                                    }}
                                    style={{
                                      padding: "6px 10px",
                                      borderRadius: 8,
                                      background:
                                        sessionPanelId === account.id
                                          ? "rgba(22,101,52,0.12)"
                                          : undefined,
                                    }}
                                  >
                                    {t("sessionConnect")}
                                  </button>
                                ) : null}
                                {account.platform === "rubika" ? (
                                  <>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setSessionPanelId(null);
                                        setRubikaUserPanelId((current) =>
                                          current === account.id ? null : account.id,
                                        );
                                      }}
                                      style={{
                                        padding: "6px 10px",
                                        borderRadius: 8,
                                        background:
                                          rubikaUserPanelId === account.id
                                            ? "rgba(180,83,9,0.12)"
                                            : undefined,
                                      }}
                                    >
                                      {t("rubikaUserLoginButton")}
                                    </button>
                                    <TooltipHint text="ورود تعاملی با شماره موبایل و کد پیامکی برای حالت user_account (اکانت شخصی روبیکا) — جدا از توکن بات بالا" />
                                  </>
                                ) : null}
                                  </>
                                ) : null}
                              </div>
                            </td>
                          </tr>
                          {waPanelId === account.id && account.platform === "whatsapp" ? (
                            <tr>
                              <td colSpan={9} style={{ padding: "0 12px 12px" }}>
                                {EVOLUTION_MODE ? (
                                  <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                                    <WhatsAppEvolutionPanel
                                      accountId={account.id}
                                      accountLabel={account.label ?? undefined}
                                      onAssignProxy={() =>
                                        setNotice(
                                          "برای تخصیص پروکسی، فرم «تخصیص Proxy» را در همین بخش تکمیل و ذخیره کنید.",
                                        )
                                      }
                                    />
                                    <ProxyAssignmentForm
                                      accountId={account.id}
                                      accountLabel={account.label ?? undefined}
                                      isConnected={false}
                                      onAssign={(proxyData) =>
                                        handleAssignProxy(account.id, proxyData)
                                      }
                                    />
                                  </div>
                                ) : (
                                  <WhatsAppWebPanel
                                    accountId={account.id}
                                    accountPhone={account.account_identifier}
                                    onRegistered={() => void loadAccounts()}
                                  />
                                )}
                              </td>
                            </tr>
                          ) : null}
                          {sessionPanelId === account.id && isApiTokenPlatform(account.platform) ? (
                            <tr>
                              <td colSpan={9} style={{ padding: "0 12px 12px" }}>
                                <ApiTokenSessionPanel
                                  accountId={account.id}
                                  platform={account.platform}
                                  accountIdentifier={account.account_identifier}
                                  onRegistered={() => void loadAccounts()}
                                />
                              </td>
                            </tr>
                          ) : null}
                          {rubikaUserPanelId === account.id && account.platform === "rubika" ? (
                            <tr>
                              <td colSpan={9} style={{ padding: "0 12px 12px" }}>
                                <RubikaUserAccountLoginPanel
                                  accountId={account.id}
                                  accountPhone={account.account_identifier}
                                  runtimeStatus={account.runtime_status ?? account.runtime?.runtime_status}
                                  runtimeLabel={
                                    account.runtime_status_label ?? account.runtime?.runtime_status_label
                                  }
                                  onRegistered={() => void loadAccounts()}
                                />
                              </td>
                            </tr>
                          ) : null}
                          {editingId === account.id && editForm ? (
                            <tr>
                              <td colSpan={9} style={{ padding: 12, background: "rgba(0,0,0,0.02)" }}>
                                <form
                                  onSubmit={(e) => void handleSaveEdit(e)}
                                  style={{
                                    display: "grid",
                                    gap: 10,
                                    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                                  }}
                                >
                                  <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                                    <span>{t("accountIdentifier")}</span>
                                    <input
                                      value={editForm.account_identifier}
                                      onChange={(e) =>
                                        setEditForm((f) =>
                                          f ? { ...f, account_identifier: e.target.value } : f,
                                        )
                                      }
                                      required
                                      style={inputStyle}
                                    />
                                  </label>
                                  <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                                    <span>{t("label")}</span>
                                    <input
                                      value={editForm.label}
                                      onChange={(e) =>
                                        setEditForm((f) => (f ? { ...f, label: e.target.value } : f))
                                      }
                                      style={inputStyle}
                                    />
                                  </label>
                                  <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                                    <span>{t("proxyUrl")}</span>
                                    <input
                                      value={editForm.proxy_url}
                                      onChange={(e) =>
                                        setEditForm((f) =>
                                          f ? { ...f, proxy_url: e.target.value } : f,
                                        )
                                      }
                                      style={inputStyle}
                                    />
                                  </label>
                                  <label style={{ display: "grid", gap: 4, fontSize: 13 }}>
                                    <span>{t("status")}</span>
                                    <select
                                      value={editForm.status}
                                      onChange={(e) =>
                                        setEditForm((f) =>
                                          f
                                            ? {
                                                ...f,
                                                status: e.target.value as AccountStatusOption,
                                              }
                                            : f,
                                        )
                                      }
                                      style={inputStyle}
                                    >
                                      {ACCOUNT_STATUS_OPTIONS.map((s) => (
                                        <option key={s} value={s}>
                                          {accountStatusLabel(s, t)}
                                        </option>
                                      ))}
                                    </select>
                                  </label>
                                  <div style={{ display: "flex", gap: 8, alignItems: "end" }}>
                                    <button
                                      type="submit"
                                      disabled={saving}
                                      style={{ padding: "8px 12px", borderRadius: 8 }}
                                    >
                                      {saving ? t("loading") : t("save")}
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setEditingId(null);
                                        setEditForm(null);
                                      }}
                                      style={{ padding: "8px 12px", borderRadius: 8 }}
                                    >
                                      {t("cancel")}
                                    </button>
                                  </div>
                                </form>
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                  </TableWrap>
                )}
              </div>
              <ConfirmDialog
                open={archiveConfirmId != null}
                title="آرشیو کردن اکانت"
                message="این اکانت از لیست فعال خارج می‌شود و تا زمان بازیابی برای ارسال استفاده نخواهد شد. سوابق آن حذف نمی‌شود."
                confirmLabel="آرشیو"
                cancelLabel={t("cancel")}
                confirmLoading={archiveLoading}
                cancelDisabled={archiveLoading}
                onCancel={() => {
                  if (archiveLoading) return;
                  setArchiveConfirmId(null);
                }}
                onConfirm={() => void handleArchiveConfirmed()}
              />
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
