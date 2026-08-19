import { apiFetch } from "@/lib/api";
import type { ContactsSearchResponse } from "@/types/contacts";

export async function searchContacts(params: {
  q: string;
  limit?: number;
  offset?: number;
}): Promise<ContactsSearchResponse> {
  const search = new URLSearchParams();
  search.set("q", params.q);
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));

  const response = await apiFetch(`/contacts/search?${search.toString()}`);
  return response.json() as Promise<ContactsSearchResponse>;
}

