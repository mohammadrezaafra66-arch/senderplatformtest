import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import { deleteContact, fetchContacts, searchContacts } from "@/lib/contacts-api";

describe("contacts API helpers", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue({
      json: async () => ({
        items: [],
        total_count: 0,
        limit: 50,
        offset: 0,
      }),
    } as Response);
  });

  it("fetchContacts calls GET /contacts with default pagination", async () => {
    await fetchContacts();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/contacts?limit=50&offset=0");
  });

  it("fetchContacts passes search, filters, and sort params", async () => {
    await fetchContacts({
      q: "0912",
      limit: 25,
      offset: 50,
      import_batch_id: 7,
      date_from: "2026-01-01",
      date_to: "2026-12-31",
      sort: "name_asc",
    });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe(
      "/contacts?limit=25&offset=50&q=0912&import_batch_id=7&date_from=2026-01-01&date_to=2026-12-31&sort=name_asc",
    );
  });

  it("searchContacts still calls GET /contacts/search", async () => {
    await searchContacts({ q: "ali", limit: 10, offset: 0 });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/contacts/search?q=ali&limit=10&offset=0");
  });

  it("deleteContact POSTs to /contacts/{id}/delete once", async () => {
    vi.mocked(apiFetch).mockResolvedValue({
      json: async () => ({
        success: true,
        contact_id: 12,
        already_deleted: false,
        deleted_at: "2026-09-02T12:00:00Z",
        message: "Contact deleted successfully.",
      }),
    } as Response);

    await deleteContact(12);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/contacts/12/delete", {
      method: "POST",
      token: undefined,
      headers: undefined,
      body: undefined,
    });
  });
});
