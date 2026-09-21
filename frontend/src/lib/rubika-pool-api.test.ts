import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import { fetchRubikaAccounts } from "@/lib/rubika-api";

describe("fetchRubikaAccounts pool query", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockResolvedValue({
      json: async () => ({ items: [], total_count: 0 }),
    } as Response);
  });

  it("loads the pool list without requesting archived accounts", async () => {
    await fetchRubikaAccounts();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [path] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe("/rubika/accounts");
    expect(String(path)).not.toContain("archived=true");
  });
});
