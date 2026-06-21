import Head from "next/head";
import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { ApiTokenSessionPanel } from "@/components/ApiTokenSessionPanel";
import { WhatsAppWebPanel } from "@/components/WhatsAppWebPanel";
import { Button, PageContent } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  createAccount,
  fetchAccounts,
  testAccountConnection,
  updateAccount,
} from "@/lib/accounts-api";
import { useAuth } from "@/state/auth";
import type {
  AccountItem,
  AccountStatusOption,
  PlatformOption,
} from "@/types/account";
import {
  ACCOUNT_STATUS_OPTIONS,
  accountStatusColor,
  accountStatusLabel,
  PLATFORM_OPTIONS,
} from "@/utils/account-status";
import { toJalaliDateTime } from "@/utils/jalali";
import { canManageAccounts } from "@/utils/permissions";

const panelStyle: React.CSSProperties = {
  marginTop: 16,
  border: "1px solid rgba(0,0,0,0.12)",
  borderRadius: 12,
  overflow: "hidden",
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

  const [platformFilter, setPlatformFilter] = useState<PlatformOption | "">("");
  const [items, setItems] = useState<AccountItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateFormState>(defaultCreateForm);
  const [creating, setCreating] = useState(false);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<EditFormState | null>(null);
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [waPanelId, setWaPanelId] = useState<number | null>(null);
  const [sessionPanelId, setSessionPanelId] = useState<number | null>(null);

  const loadAccounts = useCallback(async () => {
    if (!canManage) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await fetchAccounts(platformFilter || undefined);
      setItems(data.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("accountsLoadError"));
    } finally {
      setLoading(false);
    }
  }, [canManage, platformFilter, t]);

  useEffect(() => {
    void loadAccounts();
  }, [loadAccounts]);

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
        status: createForm.status,
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

  async function handleTestConnection(accountId: number) {
    if (!canManage) return;
    setTestingId(accountId);
    setError(null);
    setNotice(null);
    try {
      const result = await testAccountConnection(accountId);
      setNotice(
        result.success
          ? `${t("testConnectionSuccess")}: ${result.message}`
          : `${t("testConnectionFailed")}: ${result.error ?? result.message}`,
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setTestingId(null);
    }
  }

  return (
    <>
      <Head>
        <title>{t("accounts")}</title>
      </Head>
      <Layout title={t("accounts")}>
        <PageContent>
          {!canManage ? (
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
                    {p}
                  </button>
                ))}
              </div>

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
              </div>

              {showCreate ? (
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
                      onChange={(e) =>
                        setCreateForm((f) => ({
                          ...f,
                          platform: e.target.value as PlatformOption,
                        }))
                      }
                      style={inputStyle}
                    >
                      {PLATFORM_OPTIONS.map((p) => (
                        <option key={p} value={p}>
                          {p}
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
                      value={createForm.status}
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
                  <div style={{ padding: 12, opacity: 0.75 }}>{t("noAccounts")}</div>
                ) : (
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                    <thead>
                      <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                        <th style={{ padding: 10, textAlign: "right" }}>#</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("platform")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("label")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("accountIdentifier")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("status")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("lastUsedAt")}</th>
                        <th style={{ padding: 10, textAlign: "right" }}>{t("actions")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((account) => (
                        <Fragment key={account.id}>
                          <tr style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                            <td style={{ padding: 10 }}>{account.id}</td>
                            <td style={{ padding: 10 }}>{account.platform}</td>
                            <td style={{ padding: 10 }}>{account.label ?? "—"}</td>
                            <td style={{ padding: 10 }}>{account.account_identifier ?? "—"}</td>
                            <td style={{ padding: 10, color: accountStatusColor(account.status) }}>
                              {accountStatusLabel(account.status, t)}
                            </td>
                            <td style={{ padding: 10, fontSize: 13 }}>
                              {account.last_used_at
                                ? toJalaliDateTime(account.last_used_at)
                                : "—"}
                            </td>
                            <td style={{ padding: 10 }}>
                              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                                <button
                                  type="button"
                                  onClick={() => startEdit(account)}
                                  style={{ padding: "6px 10px", borderRadius: 8 }}
                                >
                                  {t("editAccount")}
                                </button>
                                <button
                                  type="button"
                                  disabled={testingId === account.id}
                                  onClick={() => void handleTestConnection(account.id)}
                                  style={{ padding: "6px 10px", borderRadius: 8 }}
                                >
                                  {testingId === account.id ? t("loading") : t("testConnection")}
                                </button>
                                {account.platform === "whatsapp" ? (
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
                                    {t("waWebConnect")}
                                  </button>
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
                              </div>
                            </td>
                          </tr>
                          {waPanelId === account.id && account.platform === "whatsapp" ? (
                            <tr>
                              <td colSpan={7} style={{ padding: "0 12px 12px" }}>
                                <WhatsAppWebPanel
                                  accountId={account.id}
                                  accountPhone={account.account_identifier}
                                  onRegistered={() => void loadAccounts()}
                                />
                              </td>
                            </tr>
                          ) : null}
                          {sessionPanelId === account.id && isApiTokenPlatform(account.platform) ? (
                            <tr>
                              <td colSpan={7} style={{ padding: "0 12px 12px" }}>
                                <ApiTokenSessionPanel
                                  accountId={account.id}
                                  platform={account.platform}
                                  accountIdentifier={account.account_identifier}
                                  onRegistered={() => void loadAccounts()}
                                />
                              </td>
                            </tr>
                          ) : null}
                          {editingId === account.id && editForm ? (
                            <tr>
                              <td colSpan={7} style={{ padding: 12, background: "rgba(0,0,0,0.02)" }}>
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
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
