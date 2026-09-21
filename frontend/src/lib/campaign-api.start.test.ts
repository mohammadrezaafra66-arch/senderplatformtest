import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import {
  campaignSenderSaveNoticeKey,
  startCampaign,
  updateCampaignAccounts,
} from "@/lib/campaign-api";

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

describe("updateCampaignAccounts API contract", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("saves senders with PUT accounts and never POSTs prepare", async () => {
    const mockFetch = vi.mocked(apiFetch);
    mockFetch.mockResolvedValue({
      json: async () => ({
        campaign_id: 5,
        account_ids: [174, 175],
        sender_accounts: [],
        auto_prepare: {
          attempted: false,
          prepared: false,
          skipped: true,
          skip_reason: "accounts_update_does_not_prepare",
        },
      }),
    } as Response);

    const result = await updateCampaignAccounts(5, [174, 175]);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [path, init] = mockFetch.mock.calls[0];
    expect(path).toBe("/campaigns/5/accounts");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ account_ids: [174, 175] });
    expect(mockFetch.mock.calls.some(([calledPath]) => String(calledPath).includes("/prepare"))).toBe(
      false,
    );
    expect(result.auto_prepare?.skipped).toBe(true);
    expect(campaignSenderSaveNoticeKey(result)).toBe("senderAccountsSaved");
    expect(campaignSenderSaveNoticeKey(result)).not.toBe("campaignPrepareSuccess");
  });

  it("keeps the sender-saved notice when auto_prepare is skipped", () => {
    expect(
      campaignSenderSaveNoticeKey({
        auto_prepare: {
          attempted: false,
          prepared: false,
          skipped: true,
          skip_reason: "accounts_update_does_not_prepare",
        },
      }),
    ).toBe("senderAccountsSaved");
  });
});
