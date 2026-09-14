import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import {
  archiveAccount,
  fetchAccounts,
  restoreAccount,
} from "@/lib/accounts-api";
import {
  archiveCampaign,
  fetchCampaigns,
  restoreCampaign,
} from "@/lib/campaign-api";

describe("archive API helpers", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue({
      json: async () => ({ message: "ok", status: "archived", entity_id: 1 }),
    } as Response);
  });

  it("archiveAccount POSTs to /accounts/{id}/archive once", async () => {
    await archiveAccount(42, { token: "test-token" });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/accounts/42/archive", {
      method: "POST",
      token: "test-token",
      headers: undefined,
      body: undefined,
    });
  });

  it("restoreAccount POSTs to /accounts/{id}/restore once", async () => {
    await restoreAccount(7, "tok");
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/accounts/7/restore", {
      method: "POST",
      token: "tok",
    });
  });

  it("fetchAccounts passes archived and q query params", async () => {
    await fetchAccounts({ archived: true, q: "0912", platform: "telegram" });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe("/accounts?platform=telegram&archived=true&q=0912");
  });

  it("archiveCampaign POSTs to /campaigns/{id}/archive once", async () => {
    await archiveCampaign(99);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/campaigns/99/archive", {
      method: "POST",
      token: undefined,
      headers: undefined,
      body: undefined,
    });
  });

  it("restoreCampaign POSTs to /campaigns/{id}/restore once", async () => {
    await restoreCampaign(11);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith("/campaigns/11/restore", {
      method: "POST",
      token: undefined,
    });
  });

  it("fetchCampaigns passes archived and q query params", async () => {
    await fetchCampaigns({ limit: 10, offset: 0, archived: true, q: "promo" });
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe("/campaigns?limit=10&offset=0&archived=true&q=promo");
  });
});
