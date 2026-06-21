import { apiFetch } from "@/lib/api";
import type {
  ContactsPreviewResponse,
  ImportCommitPayload,
  ImportCommitResult,
} from "@/types/contacts-import";

const ALLOWED_EXTENSIONS = [".xlsx", ".xls", ".xlsm"];

export function isAllowedImportFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

export async function previewContactsImport(
  file: File,
  sheetName?: string,
): Promise<ContactsPreviewResponse> {
  const form = new FormData();
  form.append("file", file);
  if (sheetName?.trim()) {
    form.append("sheet_name", sheetName.trim());
  }

  const response = await apiFetch("/imports/contacts/preview", {
    method: "POST",
    body: form,
  });
  return response.json() as Promise<ContactsPreviewResponse>;
}

export async function commitContactsImport(
  payload: ImportCommitPayload,
): Promise<ImportCommitResult> {
  const response = await apiFetch("/imports/contacts/commit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<ImportCommitResult>;
}
