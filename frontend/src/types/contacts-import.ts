export type ImportPreviewRow = {
  row_index: number;
  status: "valid" | "invalid" | "duplicate" | string;
  is_valid: boolean;
  error_code: string | null;
  error_message: string | null;
  normalized_data: {
    first_name: string | null;
    last_name: string | null;
    phone_e164: string | null;
    telegram_hint?: string | null;
    locale?: string;
    extra_variables?: Record<string, string>;
  };
  raw_data: Record<string, string | null>;
};

export type ImportPreviewData = {
  total_rows: number;
  valid_rows_count: number;
  invalid_rows_count: number;
  duplicate_rows_count: number;
  detected_columns: string[];
  column_mapping: Record<string, string>;
  rows: ImportPreviewRow[];
  errors: { code: string; message: string }[];
  sheet_name?: string | null;
};

export type ContactsPreviewResponse = {
  status: "preview_ready" | "preview_failed" | string;
  stored_file_name: string;
  original_file_name: string;
  file_path: string;
  sheet_name: string | null;
  preview: ImportPreviewData;
};

export type ImportCommitPayload = {
  file_path: string;
  original_file_name: string;
  stored_file_name: string;
  sheet_name?: string | null;
  uploaded_by?: string | null;
};

export type ImportCommitResult = {
  status: "committed" | "failed" | string;
  import_batch_id: number;
  total_rows: number;
  created_contacts_count: number;
  invalid_rows_count: number;
  duplicate_rows_count: number;
  errors_count: number;
  message: string;
};
