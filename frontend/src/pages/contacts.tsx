import Head from "next/head";
import Link from "next/link";
import { useCallback, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import {
  Alert,
  Button,
  EmptyState,
  PageContent,
  Panel,
  PanelContent,
  TableWrap,
  inputClassName,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  commitContactsImport,
  isAllowedImportFile,
  previewContactsImport,
} from "@/lib/contacts-import-api";
import { useAuth } from "@/state/auth";
import type { ContactsPreviewResponse, ImportCommitResult } from "@/types/contacts-import";
import { canCreateCampaign, canUploadContacts } from "@/utils/permissions";

const panelStyle: React.CSSProperties = {
  marginTop: 16,
  border: "1px solid rgba(0,0,0,0.12)",
  borderRadius: 12,
  overflow: "hidden",
};

const PREVIEW_ROW_LIMIT = 40;

function rowStatusColor(status: string): string {
  if (status === "valid") return "#166534";
  if (status === "duplicate") return "#b45309";
  if (status === "invalid") return "#991b1b";
  return "inherit";
}

export default function ContactsPage() {
  const { t } = useTranslation();
  const { role, username } = useAuth();
  const canUpload = canUploadContacts(role);
  const canCreate = canCreateCampaign(role);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [dragActive, setDragActive] = useState(false);
  const [sheetName, setSheetName] = useState("");
  const [loading, setLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<ContactsPreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<ImportCommitResult | null>(null);

  const resetState = useCallback(() => {
    setPreviewResult(null);
    setCommitResult(null);
    setError(null);
  }, []);

  async function handleFile(file: File) {
    if (!canUpload) return;
    if (!isAllowedImportFile(file)) {
      setError(t("invalidImportFileType"));
      return;
    }

    resetState();
    setLoading(true);
    try {
      const result = await previewContactsImport(file, sheetName || undefined);
      setPreviewResult(result);
      if (result.status !== "preview_ready") {
        const previewErrors = result.preview.errors ?? [];
        if (previewErrors.length > 0) {
          setError(previewErrors.map((e) => e.message).join(" • "));
        } else {
          setError(t("previewFailed"));
        }
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("previewFailed"));
    } finally {
      setLoading(false);
    }
  }

  function onFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) void handleFile(file);
    e.target.value = "";
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragActive(false);
    if (!canUpload) return;
    const file = e.dataTransfer.files?.[0];
    if (file) void handleFile(file);
  }

  async function handleCommit() {
    if (!canUpload || !previewResult || previewResult.status !== "preview_ready") return;

    setCommitting(true);
    setError(null);
    try {
      const result = await commitContactsImport({
        file_path: previewResult.file_path,
        original_file_name: previewResult.original_file_name,
        stored_file_name: previewResult.stored_file_name,
        sheet_name: previewResult.sheet_name,
        uploaded_by: username ?? undefined,
      });
      setCommitResult(result);
      if (result.status !== "committed") {
        setError(result.message);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("commitFailed"));
    } finally {
      setCommitting(false);
    }
  }

  const preview = previewResult?.preview;
  const previewRows = preview?.rows.slice(0, PREVIEW_ROW_LIMIT) ?? [];

  return (
    <>
      <Head>
        <title>{t("contacts")}</title>
      </Head>
      <Layout title={t("contacts")}>
        <PageContent>
          {!canUpload ? (
            <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.14)" }}>
              {t("notAllowed")}
            </div>
          ) : (
            <>
              <div
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragActive(true);
                }}
                onDragLeave={() => setDragActive(false)}
                onDrop={onDrop}
                style={{
                  border: dragActive
                    ? "2px dashed rgba(22, 101, 52, 0.6)"
                    : "1px dashed rgba(0,0,0,0.25)",
                  borderRadius: 12,
                  padding: 16,
                  background: dragActive ? "rgba(22, 101, 52, 0.04)" : "rgba(0,0,0,0.02)",
                }}
              >
                <div style={{ fontWeight: 800, marginBottom: 8 }}>{t("uploadContactsTitle")}</div>
                <div style={{ opacity: 0.75, fontSize: 14, marginBottom: 12 }}>
                  {t("uploadContactsHint")}
                </div>

                <label style={{ display: "grid", gap: 6, marginBottom: 12, maxWidth: 320 }}>
                  <span style={{ fontSize: 14 }}>{t("sheetNameOptional")}</span>
                  <input
                    value={sheetName}
                    onChange={(e) => setSheetName(e.target.value)}
                    placeholder={t("sheetNamePlaceholder")}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 8,
                      border: "1px solid rgba(0,0,0,0.2)",
                    }}
                  />
                </label>

                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx,.xls,.xlsm"
                  style={{ display: "none" }}
                  onChange={onFileInputChange}
                />
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => fileInputRef.current?.click()}
                  style={{
                    padding: "10px 12px",
                    borderRadius: 10,
                    border: "1px solid rgba(0,0,0,0.2)",
                    background: "rgba(0,0,0,0.04)",
                    cursor: loading ? "wait" : "pointer",
                  }}
                >
                  {loading ? t("loading") : t("selectFile")}
                </button>
              </div>

              {error ? (
                <div role="alert" style={{ marginTop: 12, color: "#991b1b", fontSize: 14 }}>
                  {error}
                </div>
              ) : null}

              {previewResult && preview ? (
                <div style={panelStyle}>
                  <div
                    style={{
                      padding: "10px 12px",
                      background: "rgba(0,0,0,0.02)",
                      borderBottom: "1px solid rgba(0,0,0,0.08)",
                      fontWeight: 700,
                    }}
                  >
                    {t("preview")}: {previewResult.original_file_name}
                  </div>
                  <div style={{ padding: 12, fontSize: 14, display: "grid", gap: 10 }}>
                    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                      <span>
                        {t("totalRows")}: {preview.total_rows}
                      </span>
                      <span style={{ color: "#166534" }}>
                        {t("validRows")}: {preview.valid_rows_count}
                      </span>
                      <span style={{ color: "#991b1b" }}>
                        {t("invalidRows")}: {preview.invalid_rows_count}
                      </span>
                      <span style={{ color: "#b45309" }}>
                        {t("duplicateRows")}: {preview.duplicate_rows_count}
                      </span>
                    </div>

                    {Object.keys(preview.column_mapping).length > 0 ? (
                      <div>
                        <div style={{ fontWeight: 600, marginBottom: 6 }}>{t("columnMapping")}</div>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                          {Object.entries(preview.column_mapping).map(([key, col]) => (
                            <span
                              key={key}
                              style={{
                                fontSize: 13,
                                padding: "4px 8px",
                                borderRadius: 8,
                                background: "rgba(0,0,0,0.04)",
                              }}
                            >
                              {key} ← {col}
                            </span>
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {previewResult.status === "preview_ready" ? (
                      <button
                        type="button"
                        disabled={committing || !!commitResult}
                        onClick={() => void handleCommit()}
                        style={{
                          justifySelf: "start",
                          padding: "10px 14px",
                          borderRadius: 10,
                          border: "1px solid rgba(0,0,0,0.2)",
                          cursor: committing ? "wait" : "pointer",
                          opacity: commitResult ? 0.6 : 1,
                        }}
                      >
                        {committing ? t("loading") : t("commitImport")}
                      </button>
                    ) : null}
                  </div>

                  {previewRows.length > 0 ? (
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                      <thead>
                        <tr style={{ background: "rgba(0,0,0,0.02)" }}>
                          <th style={{ padding: 8, textAlign: "right" }}>#</th>
                          <th style={{ padding: 8, textAlign: "right" }}>{t("name")}</th>
                          <th style={{ padding: 8, textAlign: "right" }}>{t("phone")}</th>
                          <th style={{ padding: 8, textAlign: "right" }}>{t("status")}</th>
                          <th style={{ padding: 8, textAlign: "right" }}>{t("error")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previewRows.map((row) => {
                          const nd = row.normalized_data;
                          const fullName =
                            [nd.first_name, nd.last_name].filter(Boolean).join(" ") || "—";
                          return (
                            <tr key={row.row_index} style={{ borderTop: "1px solid rgba(0,0,0,0.06)" }}>
                              <td style={{ padding: 8 }}>{row.row_index}</td>
                              <td style={{ padding: 8 }}>{fullName}</td>
                              <td style={{ padding: 8 }}>{nd.phone_e164 ?? "—"}</td>
                              <td style={{ padding: 8, color: rowStatusColor(row.status) }}>
                                {row.status}
                              </td>
                              <td style={{ padding: 8, fontSize: 12, opacity: 0.85 }}>
                                {row.error_message ?? "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : null}

                  {preview.rows.length > PREVIEW_ROW_LIMIT ? (
                    <div style={{ padding: 10, fontSize: 13, opacity: 0.75 }}>
                      {t("previewRowLimit", { shown: PREVIEW_ROW_LIMIT, total: preview.rows.length })}
                    </div>
                  ) : null}
                </div>
              ) : null}

              {commitResult ? (
                <div style={panelStyle}>
                  <div
                    style={{
                      padding: "10px 12px",
                      background: "rgba(0,0,0,0.02)",
                      borderBottom: "1px solid rgba(0,0,0,0.08)",
                      fontWeight: 700,
                    }}
                  >
                    {t("importResult")}
                  </div>
                  <div style={{ padding: 12, fontSize: 14, display: "grid", gap: 8 }}>
                    <div>
                      {t("importBatchId")}: <strong>{commitResult.import_batch_id}</strong>
                    </div>
                    <div>{commitResult.message}</div>
                    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                      <span>
                        {t("createdContacts")}: {commitResult.created_contacts_count}
                      </span>
                      <span>
                        {t("invalidRows")}: {commitResult.invalid_rows_count}
                      </span>
                      <span>
                        {t("duplicateRows")}: {commitResult.duplicate_rows_count}
                      </span>
                    </div>
                    {canCreate && commitResult.status === "committed" ? (
                      <Link
                        href={`/campaigns/create?import_batch_id=${commitResult.import_batch_id}`}
                        style={{
                          display: "inline-block",
                          marginTop: 4,
                          padding: "10px 12px",
                          borderRadius: 10,
                          border: "1px solid rgba(0,0,0,0.2)",
                          background: "rgba(0,0,0,0.04)",
                        }}
                      >
                        {t("createCampaignFromImport")}
                      </Link>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
