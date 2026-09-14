import Head from "next/head";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Layout } from "@/components/Layout";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import {
  Alert,
  Button,
  EmptyState,
  FormField,
  PageContent,
  Panel,
  TableWrap,
  inputClassName,
  selectClassName,
  tableClassName,
} from "@/components/ui";
import { ApiError } from "@/lib/api";
import { fetchContacts, deleteContact } from "@/lib/contacts-api";
import {
  commitContactsImport,
  isAllowedImportFile,
  previewContactsImport,
} from "@/lib/contacts-import-api";
import { useAuth } from "@/state/auth";
import type { ContactListItem, ContactSort } from "@/types/contacts";
import type { ContactsPreviewResponse, ImportCommitResult } from "@/types/contacts-import";
import { toJalaliDateTime } from "@/utils/jalali";
import { canCreateCampaign, canDeleteContacts, canUploadContacts, canViewContacts } from "@/utils/permissions";

const PREVIEW_ROW_LIMIT = 40;
const PAGE_SIZE = 50;

function contactDisplayName(contact: ContactListItem): string {
  if (contact.full_name?.trim()) return contact.full_name.trim();
  const joined = [contact.first_name, contact.last_name].filter(Boolean).join(" ");
  return joined || "—";
}

function importSourceLabel(contact: ContactListItem): string {
  if (contact.source_import_file_name) return contact.source_import_file_name;
  if (contact.source_import_id != null) return `دسته #${contact.source_import_id}`;
  return "—";
}

function rowStatusColor(status: string): string {
  if (status === "valid") return "#166534";
  if (status === "duplicate") return "#b45309";
  if (status === "invalid") return "#991b1b";
  return "inherit";
}

export default function ContactsPage() {
  const { t } = useTranslation();
  const { role, username } = useAuth();
  const canView = canViewContacts(role);
  const canUpload = canUploadContacts(role);
  const canDelete = canDeleteContacts(role);
  const canCreate = canCreateCampaign(role);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [items, setItems] = useState<ContactListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [listNotice, setListNotice] = useState<string | null>(null);
  const [deleteConfirmContact, setDeleteConfirmContact] = useState<ContactListItem | null>(null);
  const [deleteLoading, setDeleteLoading] = useState(false);

  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [importBatchFilter, setImportBatchFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [sort, setSort] = useState<ContactSort>("created_at_desc");

  const [dragActive, setDragActive] = useState(false);
  const [sheetName, setSheetName] = useState("");
  const [importLoading, setImportLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [previewResult, setPreviewResult] = useState<ContactsPreviewResponse | null>(null);
  const [commitResult, setCommitResult] = useState<ImportCommitResult | null>(null);

  const loadContacts = useCallback(async () => {
    if (!canView) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setListError(null);
    setListNotice(null);
    try {
      const importBatchId = importBatchFilter.trim()
        ? Number.parseInt(importBatchFilter.trim(), 10)
        : undefined;
      const data = await fetchContacts({
        q: searchQuery || undefined,
        limit: PAGE_SIZE,
        offset,
        import_batch_id:
          importBatchId != null && Number.isFinite(importBatchId) ? importBatchId : undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        sort,
      });
      setItems(data.items);
      setTotal(data.total_count);
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setLoading(false);
    }
  }, [canView, searchQuery, offset, importBatchFilter, dateFrom, dateTo, sort, t]);

  useEffect(() => {
    void loadContacts();
  }, [loadContacts]);

  const resetImportState = useCallback(() => {
    setPreviewResult(null);
    setCommitResult(null);
    setImportError(null);
  }, []);

  async function handleFile(file: File) {
    if (!canUpload) return;
    if (!isAllowedImportFile(file)) {
      setImportError(t("invalidImportFileType"));
      return;
    }

    resetImportState();
    setImportLoading(true);
    try {
      const result = await previewContactsImport(file, sheetName || undefined);
      setPreviewResult(result);
      if (result.status !== "preview_ready") {
        const previewErrors = result.preview.errors ?? [];
        if (previewErrors.length > 0) {
          setImportError(previewErrors.map((e) => e.message).join(" • "));
        } else {
          setImportError(t("previewFailed"));
        }
      }
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : t("previewFailed"));
    } finally {
      setImportLoading(false);
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
    setImportError(null);
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
        setImportError(result.message);
      } else {
        setOffset(0);
        await loadContacts();
      }
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : t("commitFailed"));
    } finally {
      setCommitting(false);
    }
  }

  function applySearch() {
    setOffset(0);
    setSearchQuery(searchInput.trim());
  }

  function clearFilters() {
    setSearchInput("");
    setSearchQuery("");
    setImportBatchFilter("");
    setDateFrom("");
    setDateTo("");
    setSort("created_at_desc");
    setOffset(0);
  }

  async function handleDeleteConfirmed() {
    if (!canDelete || !deleteConfirmContact || deleteLoading) return;
    const contactId = deleteConfirmContact.contact_id;
    setDeleteLoading(true);
    setListError(null);
    try {
      const result = await deleteContact(contactId);
      setDeleteConfirmContact(null);
      setItems((prev) => prev.filter((item) => item.contact_id !== contactId));
      setTotal((prev) => {
        const next = Math.max(0, prev - (result.already_deleted ? 0 : 1));
        return next;
      });
      if (offset > 0 && items.length === 1) {
        setOffset((prev) => Math.max(0, prev - PAGE_SIZE));
      }
      setListNotice(result.already_deleted ? "مخاطب قبلاً حذف شده بود." : "مخاطب با موفقیت حذف شد.");
    } catch (err) {
      setListError(err instanceof ApiError ? err.message : t("actionFailed"));
    } finally {
      setDeleteLoading(false);
    }
  }

  const preview = previewResult?.preview;
  const previewRows = preview?.rows.slice(0, PREVIEW_ROW_LIMIT) ?? [];
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <>
      <Head>
        <title>{t("contacts")}</title>
      </Head>
      <Layout title={t("contacts")}>
        <PageContent>
          {!canView ? (
            <Panel>
              <EmptyState>{t("notAllowed")}</EmptyState>
            </Panel>
          ) : (
            <>
              <Panel title={`${t("contacts")} (${total})`} flushTable>
                <div className="mmp-panel__content">
                  <div className="mmp-stack" style={{ marginBottom: 12 }}>
                    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "end" }}>
                      <FormField label="جستجو">
                        <input
                          className={inputClassName}
                          value={searchInput}
                          onChange={(e) => setSearchInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") applySearch();
                          }}
                          placeholder="شماره، نام یا شناسه"
                          style={{ minWidth: 220 }}
                        />
                      </FormField>
                      <Button type="button" size="sm" onClick={applySearch}>
                        جستجو
                      </Button>
                      <Button type="button" size="sm" variant="ghost" onClick={clearFilters}>
                        همه
                      </Button>
                    </div>

                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                      <FormField label="دسته Import">
                        <input
                          className={inputClassName}
                          value={importBatchFilter}
                          onChange={(e) => {
                            setImportBatchFilter(e.target.value);
                            setOffset(0);
                          }}
                          placeholder="شناسه دسته"
                          style={{ width: 120 }}
                        />
                      </FormField>
                      <FormField label="از تاریخ">
                        <input
                          type="date"
                          className={inputClassName}
                          value={dateFrom}
                          onChange={(e) => {
                            setDateFrom(e.target.value);
                            setOffset(0);
                          }}
                        />
                      </FormField>
                      <FormField label="تا تاریخ">
                        <input
                          type="date"
                          className={inputClassName}
                          value={dateTo}
                          onChange={(e) => {
                            setDateTo(e.target.value);
                            setOffset(0);
                          }}
                        />
                      </FormField>
                      <FormField label="مرتب‌سازی">
                        <select
                          className={selectClassName}
                          value={sort}
                          onChange={(e) => {
                            setSort(e.target.value as ContactSort);
                            setOffset(0);
                          }}
                          style={{ minWidth: 180 }}
                        >
                          <option value="created_at_desc">جدیدترین</option>
                          <option value="created_at_asc">قدیمی‌ترین</option>
                          <option value="name_asc">نام (الف-ی)</option>
                          <option value="name_desc">نام (ی-الف)</option>
                        </select>
                      </FormField>
                    </div>
                  </div>

                  {listError ? <Alert>{listError}</Alert> : null}
                  {listNotice ? <Alert variant="success">{listNotice}</Alert> : null}

                  {loading ? (
                    <EmptyState>{t("loading")}</EmptyState>
                  ) : items.length === 0 ? (
                    <EmptyState>مخاطبی یافت نشد.</EmptyState>
                  ) : (
                    <TableWrap>
                      <table className={tableClassName}>
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>{t("name")}</th>
                            <th>{t("phone")}</th>
                            <th>تاریخ ورود</th>
                            <th>منبع Import</th>
                            <th>تعداد Import</th>
                            <th>کمپین‌ها</th>
                            <th>{t("status")}</th>
                            {canDelete ? <th>{t("actions")}</th> : null}
                          </tr>
                        </thead>
                        <tbody>
                          {items.map((contact) => (
                            <tr key={contact.contact_id}>
                              <td>{contact.contact_id}</td>
                              <td>{contactDisplayName(contact)}</td>
                              <td>{contact.phone}</td>
                              <td>{toJalaliDateTime(contact.created_at)}</td>
                              <td>{importSourceLabel(contact)}</td>
                              <td>{contact.import_count > 0 ? contact.import_count : "—"}</td>
                              <td>{contact.campaign_count > 0 ? contact.campaign_count : "—"}</td>
                              <td>
                                {contact.blacklisted
                                  ? "لیست سیاه"
                                  : contact.consent_status === "blocked"
                                    ? "مسدود"
                                    : contact.eligible
                                      ? "قابل استفاده"
                                      : (contact.ineligible_reason ?? "—")}
                              </td>
                              {canDelete ? (
                                <td>
                                  <Button
                                    type="button"
                                    size="sm"
                                    disabled={deleteLoading}
                                    onClick={() => setDeleteConfirmContact(contact)}
                                    style={{
                                      color: "#991b1b",
                                      borderColor: "rgba(153,27,27,0.35)",
                                      background: "rgba(153,27,27,0.06)",
                                    }}
                                  >
                                    حذف
                                  </Button>
                                </td>
                              ) : null}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </TableWrap>
                  )}

                  <div className="mmp-stack" style={{ marginTop: 12 }}>
                    <span className="mmp-muted">
                      {total > 0
                        ? `نمایش ${rangeStart} تا ${rangeEnd} از ${total} مخاطب`
                        : "مخاطبی برای نمایش وجود ندارد"}
                    </span>
                    <div style={{ display: "flex", gap: 8 }}>
                      <Button
                        type="button"
                        size="sm"
                        disabled={offset <= 0 || loading}
                        onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                      >
                        {t("prevPage")}
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        disabled={offset + PAGE_SIZE >= total || loading}
                        onClick={() => setOffset((o) => o + PAGE_SIZE)}
                      >
                        {t("nextPage")}
                      </Button>
                    </div>
                  </div>
                </div>
              </Panel>

              <ConfirmDialog
                open={deleteConfirmContact != null}
                title="حذف مخاطب"
                message="آیا از حذف این مخاطب مطمئن هستید؟ مخاطب از لیست مخاطبین حذف می‌شود، اما سوابق قبلی آن در کمپین‌ها و پیام‌ها حفظ خواهد شد."
                confirmLabel="حذف مخاطب"
                cancelLabel={t("cancel")}
                confirmLoading={deleteLoading}
                cancelDisabled={deleteLoading}
                onCancel={() => {
                  if (deleteLoading) return;
                  setDeleteConfirmContact(null);
                }}
                onConfirm={() => void handleDeleteConfirmed()}
              />

              {canUpload ? (
                <>
                  <Panel title={t("uploadContactsTitle")} className="mmp-panel" >
                    <div className="mmp-panel__content">
                      <div
                        onDragOver={(e) => {
                          e.preventDefault();
                          setDragActive(true);
                        }}
                        onDragLeave={() => setDragActive(false)}
                        onDrop={onDrop}
                        className={`mmp-dropzone${dragActive ? " mmp-dropzone--active" : ""}`}
                      >
                        <div style={{ opacity: 0.75, fontSize: 14, marginBottom: 12 }}>
                          {t("uploadContactsHint")}
                        </div>

                        <FormField label={t("sheetNameOptional")}>
                          <input
                            className={inputClassName}
                            value={sheetName}
                            onChange={(e) => setSheetName(e.target.value)}
                            placeholder={t("sheetNamePlaceholder")}
                            style={{ maxWidth: 320 }}
                          />
                        </FormField>

                        <input
                          ref={fileInputRef}
                          type="file"
                          accept=".xlsx,.xls,.xlsm"
                          style={{ display: "none" }}
                          onChange={onFileInputChange}
                        />
                        <Button
                          type="button"
                          disabled={importLoading}
                          onClick={() => fileInputRef.current?.click()}
                        >
                          {importLoading ? t("loading") : t("selectFile")}
                        </Button>
                      </div>

                      {importError ? <Alert>{importError}</Alert> : null}

                      {previewResult && preview ? (
                        <div style={{ marginTop: 16 }}>
                          <div style={{ fontWeight: 700, marginBottom: 8 }}>
                            {t("preview")}: {previewResult.original_file_name}
                          </div>
                          <div className="mmp-stack" style={{ fontSize: 14 }}>
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

                            {previewResult.status === "preview_ready" ? (
                              <Button
                                type="button"
                                disabled={committing || !!commitResult}
                                onClick={() => void handleCommit()}
                              >
                                {committing ? t("loading") : t("commitImport")}
                              </Button>
                            ) : null}
                          </div>

                          {previewRows.length > 0 ? (
                            <TableWrap>
                              <table className={tableClassName} style={{ marginTop: 12, fontSize: 13 }}>
                                <thead>
                                  <tr>
                                    <th>#</th>
                                    <th>{t("name")}</th>
                                    <th>{t("phone")}</th>
                                    <th>{t("status")}</th>
                                    <th>{t("error")}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {previewRows.map((row) => {
                                    const nd = row.normalized_data;
                                    const fullName =
                                      [nd.first_name, nd.last_name].filter(Boolean).join(" ") || "—";
                                    return (
                                      <tr key={row.row_index}>
                                        <td>{row.row_index}</td>
                                        <td>{fullName}</td>
                                        <td>{nd.phone_e164 ?? "—"}</td>
                                        <td style={{ color: rowStatusColor(row.status) }}>
                                          {row.status}
                                        </td>
                                        <td style={{ fontSize: 12, opacity: 0.85 }}>
                                          {row.error_message ?? "—"}
                                        </td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </TableWrap>
                          ) : null}

                          {preview.rows.length > PREVIEW_ROW_LIMIT ? (
                            <div className="mmp-muted" style={{ marginTop: 8, fontSize: 13 }}>
                              {t("previewRowLimit", {
                                shown: PREVIEW_ROW_LIMIT,
                                total: preview.rows.length,
                              })}
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {commitResult ? (
                        <div style={{ marginTop: 12 }}>
                          <Alert variant="success">
                          <div className="mmp-stack">
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
                                className="mmp-btn mmp-btn--primary"
                              >
                                {t("createCampaignFromImport")}
                              </Link>
                            ) : null}
                          </div>
                          </Alert>
                        </div>
                      ) : null}
                    </div>
                  </Panel>
                </>
              ) : null}
            </>
          )}
        </PageContent>
      </Layout>
    </>
  );
}
