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

