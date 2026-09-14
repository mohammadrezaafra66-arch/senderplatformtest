export type ContactSearchItem = {
  contact_id: number;
  first_name: string | null;
  last_name: string | null;
  full_name: string | null;
  phone: string;
  consent_status: string;
  blacklisted: boolean;
  eligible: boolean;
  ineligible_reason: string | null;
};

export type ContactsSearchResponse = {
  items: ContactSearchItem[];
  total_count: number;
  limit: number;
  offset: number;
};

export type ContactListItem = {
  contact_id: number;
  first_name: string | null;
  last_name: string | null;
  full_name: string | null;
  phone: string;
  consent_status: string;
  blacklisted: boolean;
  eligible: boolean;
  ineligible_reason: string | null;
  created_at: string;
  source_import_id: number | null;
  source_import_file_name: string | null;
  source_imported_at: string | null;
  import_count: number;
  campaign_count: number;
};

export type ContactsListResponse = {
  items: ContactListItem[];
  total_count: number;
  limit: number;
  offset: number;
};

export type ContactDeleteResponse = {
  success: boolean;
  contact_id: number;
  already_deleted: boolean;
  deleted_at: string | null;
  message: string;
};

export type ContactSort = "created_at_desc" | "created_at_asc" | "name_asc" | "name_desc";
