import { apiFetch } from "@/lib/api";
import type {
  ContactDeleteResponse,
  ContactSort,
  ContactTagsResult,
  ContactUpdatePayload,
  ContactUpdateResult,
  ContactsListResponse,
  ContactsSearchResponse,
} from "@/types/contacts";

export async function fetchContacts(params?: {
  q?: string;
  limit?: number;
  offset?: number;
  import_batch_id?: number;
  date_from?: string;
  date_to?: string;
  sort?: ContactSort;
  tags?: string;
  tag_match?: "any" | "all";
}): Promise<ContactsListResponse> {
  const search = new URLSearchParams();
  search.set("limit", String(params?.limit ?? 50));
  search.set("offset", String(params?.offset ?? 0));
  if (params?.q?.trim()) search.set("q", params.q.trim());
  if (params?.import_batch_id != null) {
    search.set("import_batch_id", String(params.import_batch_id));
  }
  if (params?.date_from) search.set("date_from", params.date_from);
  if (params?.date_to) search.set("date_to", params.date_to);
  if (params?.sort) search.set("sort", params.sort);
  if (params?.tags?.trim()) search.set("tags", params.tags.trim());
  if (params?.tag_match) search.set("tag_match", params.tag_match);

  const response = await apiFetch(`/contacts?${search.toString()}`);
  return response.json() as Promise<ContactsListResponse>;
}

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

export async function deleteContact(contactId: number): Promise<ContactDeleteResponse> {
  const response = await apiFetch(`/contacts/${contactId}/delete`, {
    method: "POST",
  });
  return response.json() as Promise<ContactDeleteResponse>;
}

export async function updateContact(
  contactId: number,
  payload: ContactUpdatePayload,
): Promise<ContactUpdateResult> {
  const response = await apiFetch(`/contacts/${contactId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<ContactUpdateResult>;
}

export async function fetchContactTags(): Promise<ContactTagsResult> {
  const response = await apiFetch("/contacts/tags");
  return response.json() as Promise<ContactTagsResult>;
}
