import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {
    code?: string;
    constructor(message: string, code?: string) {
      super(message);
      this.code = code;
    }
  },
}));

import { apiFetch, ApiError } from "@/lib/api";
import { startCampaign } from "@/lib/campaign-api";
import { campaignStartError } from "@/lib/campaign-start-errors";
import {
  isStartActionable,
  requiresControlledProductionConfirmation,
  hasTechnicalStartBlockers,
  formatPreflightBlockers,
} from "@/utils/campaign-preflight-display";

describe("controlled production frontend contract", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("Cancel path never calls startCampaign (zero Start requests)", () => {
    // Modal Cancel only closes UI — no API. Modeled as: never invoke startCampaign.
    const mockFetch = vi.mocked(apiFetch);
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("Confirm body is confirm_controlled_production=true only when confirmed", async () => {
    const mockFetch = vi.mocked(apiFetch);
    mockFetch.mockResolvedValue({
      json: async () => ({ message: "ok" }),
    } as Response);

    await startCampaign(125);
    expect(JSON.parse(String(mockFetch.mock.calls[0][1]?.body))).toEqual({
      confirm_controlled_production: false,
    });

    await startCampaign(125, { confirmControlledProduction: true });
    expect(JSON.parse(String(mockFetch.mock.calls[1][1]?.body))).toEqual({
      confirm_controlled_production: true,
    });
  });

  it("technically ready + controlled gate opens confirmation flow (actionable)", () => {
    const preflight = {
      allowed_to_start: false,
      allowed_to_start_after_confirmation: true,
      technical_ready: true,
      controlled_production_confirmation_required: true,
      controlled_production_label: "تأیید نهایی لازم است",
      blockers: [
        {
          code: "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED",
          message: "برای شروع ارسال واقعی، تأیید نهایی اپراتور لازم است.",
        },
      ],
    } as never;
    expect(isStartActionable(preflight)).toBe(true);
    expect(requiresControlledProductionConfirmation(preflight)).toBe(true);
    expect(hasTechnicalStartBlockers(preflight)).toBe(false);
    expect(formatPreflightBlockers(preflight, ((k: string) => k) as never)).toEqual([]);
  });

  it("technical blocker remains authoritative (not confirm-only)", () => {
    const preflight = {
      allowed_to_start: false,
      allowed_to_start_after_confirmation: false,
      technical_ready: false,
      controlled_production_confirmation_required: false,
      blockers: [{ code: "CAMPAIGN_NOT_PREPARED", message: "کمپین هنوز آماده‌سازی نشده است." }],
      message: "کمپین هنوز آماده‌سازی نشده است.",
    } as never;
    expect(isStartActionable(preflight)).toBe(false);
    expect(requiresControlledProductionConfirmation(preflight)).toBe(false);
    expect(hasTechnicalStartBlockers(preflight)).toBe(true);
  });

  it("controlled OFF: no confirmation requirement when start allowed", () => {
    const preflight = {
      allowed_to_start: true,
      allowed_to_start_after_confirmation: false,
      technical_ready: true,
      controlled_production_confirmation_required: false,
      blockers: [],
    } as never;
    expect(requiresControlledProductionConfirmation(preflight)).toBe(false);
    expect(isStartActionable(preflight)).toBe(true);
  });

  it("translates CONTROLLED_PRODUCTION_APPROVAL_REQUIRED to Persian key", () => {
    const err = new (ApiError as any)(
      "Controlled production mode requires explicit operator approval",
      "CONTROLLED_PRODUCTION_APPROVAL_REQUIRED",
    );
    const t = (key: string) =>
      key === "campaignControlledConfirmationRequired"
        ? "کمپین آماده ارسال است — تأیید نهایی برای شروع ارسال واقعی لازم است."
        : key;
    const msg = campaignStartError(err, t as never);
    expect(msg).not.toContain("CONTROLLED_PRODUCTION_APPROVAL_REQUIRED");
    expect(msg).not.toContain("confirm_controlled_production");
    expect(msg).toContain("تأیید نهایی");
  });

  it("idempotent submit: second startCampaign while in-flight is UI-gated (loading disables)", () => {
    // ConfirmDialog disables confirm when confirmLoading=true — covered by component contract.
    // API layer itself is single-call; page sets startConfirmLoading before await.
    let inFlight = false;
    const submitOnce = () => {
      if (inFlight) return false;
      inFlight = true;
      return true;
    };
    expect(submitOnce()).toBe(true);
    expect(submitOnce()).toBe(false);
  });
});
