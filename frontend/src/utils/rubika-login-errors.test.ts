import { describe, expect, it } from "vitest";

import { isCampaignEligible } from "@/utils/sender-accounts";
import { formatResendCountdown, rubikaLoginErrorMessage } from "@/utils/rubika-login-errors";

describe("rubika login error contract", () => {
  it("maps OTP failures to Persian and hides raw codes", () => {
    expect(rubikaLoginErrorMessage("OTP_INVALID")).toBe("کد واردشده نادرست است.");
    expect(rubikaLoginErrorMessage("OTP_EXPIRED")).toContain("منقضی");
    expect(rubikaLoginErrorMessage("OTP_RATE_LIMITED")).toContain("ارسال مجدد");
    expect(rubikaLoginErrorMessage("Traceback (most recent call last)")).not.toContain("Traceback");
  });

  it("formats a visible resend countdown", () => {
    expect(formatResendCountdown(65)).toBe("1:05");
    expect(formatResendCountdown(9)).toBe("9 ثانیه");
  });
});

describe("campaign selection does not treat lifecycle active as ready", () => {
  it("blocks non-READY and requires_login even if a stale eligible flag is present", () => {
    expect(
      isCampaignEligible({
        id: 1,
        platform: "rubika",
        account_identifier: "98912",
        status: "active",
        runtime_status: "AUTHENTICATED_NO_WORKER",
        campaign_eligible: true,
      } as never),
    ).toBe(false);
    expect(
      isCampaignEligible({
        id: 2,
        platform: "rubika",
        account_identifier: "98913",
        status: "requires_login",
        runtime_status: "LOGIN_REQUIRED",
        campaign_eligible: false,
      } as never),
    ).toBe(false);
    expect(
      isCampaignEligible({
        id: 3,
        platform: "rubika",
        account_identifier: "98914",
        status: "active",
        runtime_status: "READY",
        campaign_eligible: true,
      } as never),
    ).toBe(true);
  });
});
