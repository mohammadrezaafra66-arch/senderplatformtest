import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import { startCampaign } from "@/lib/campaign-api";

describe("startCampaign API contract", () => {
  it("always serializes confirm_controlled_production as an explicit boolean", async () => {
    const mockFetch = vi.mocked(apiFetch);
    mockFetch.mockResolvedValue({
      json: async () => ({ message: "ok", accepted: true }),
    } as Response);

    await startCampaign(126);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [pathFalse, initFalse] = mockFetch.mock.calls[0];
    expect(pathFalse).toBe("/campaigns/126/start");
    expect(initFalse?.method).toBe("POST");
    expect((initFalse?.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect((initFalse?.headers as Record<string, string>)["X-Request-Id"]).toBeTruthy();
    expect(JSON.parse(String(initFalse?.body))).toEqual({
      confirm_controlled_production: false,
    });

    await startCampaign(126, { confirmControlledProduction: true });
    const [, initTrue] = mockFetch.mock.calls[1];
    expect(JSON.parse(String(initTrue?.body))).toEqual({
      confirm_controlled_production: true,
    });
    expect((initTrue?.headers as Record<string, string>)["X-Request-Id"]).toBeTruthy();
  });
});
