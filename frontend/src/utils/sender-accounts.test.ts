import { describe, expect, it } from "vitest";

import {
  isCampaignEligible,
  resolveCampaignSenderStatusLabel,
  resolveDisplayIdentity,
} from "./sender-accounts";

const t = (key: string, options?: { defaultValue?: string }) => {
  const map: Record<string, string> = {
    runtime_status_READY: "آماده ارسال",
    runtime_status_MANUAL_REVIEW: "نیازمند بررسی",
    runtime_status_LOGIN_REQUIRED: "نیاز به ورود",
    runtime_status_SESSION_ERROR: "خطای سشن",
    account_status_active: "فعال",
    senderStatusUnknown: "وضعیت نامشخص",
  };
  return map[key] ?? options?.defaultValue ?? key;
};

describe("campaign sender screenshot regression", () => {
  it("maps READY/MANUAL_REVIEW/LOGIN_REQUIRED without generic active label", () => {
    const ready = {
      id: 79,
      status: "active" as const,
      runtime_status: "READY",
      campaign_status_label: "آماده ارسال",
      campaign_eligible: true,
      display_identity: "989048241903",
    };
    const manual = {
      id: 12,
      status: "active" as const,
      runtime_status: "MANUAL_REVIEW",
      campaign_status_label: "نیازمند بررسی",
      campaign_eligible: false,
      display_identity: "989121234567",
    };
    const loginRequired = {
      id: 32,
      status: "active" as const,
      runtime_status: "LOGIN_REQUIRED",
      campaign_status_label: "نیاز به ورود",
      campaign_eligible: false,
      display_identity: "9890099887766",
    };

    expect(resolveCampaignSenderStatusLabel(ready, t)).toBe("آماده ارسال");
    expect(resolveCampaignSenderStatusLabel(manual, t)).toBe("نیازمند بررسی");
    expect(resolveCampaignSenderStatusLabel(loginRequired, t)).toBe("نیاز به ورود");

    expect(resolveCampaignSenderStatusLabel(ready, t)).not.toBe("فعال");
    expect(resolveCampaignSenderStatusLabel(manual, t)).not.toBe("فعال");
    expect(resolveCampaignSenderStatusLabel(loginRequired, t)).not.toBe("فعال");

    expect(isCampaignEligible(ready as never)).toBe(true);
    expect(isCampaignEligible(manual as never)).toBe(false);
    expect(isCampaignEligible(loginRequired as never)).toBe(false);
  });

  it("never renders index/count as display identity", () => {
    expect(resolveDisplayIdentity({ id: 1, label: "1", account_identifier: null })).toBe("اکانت #1");
    expect(resolveDisplayIdentity({ id: 79, display_identity: "989048241903" })).toBe("989048241903");
    expect(resolveDisplayIdentity({ id: 2, label: "1/1", account_identifier: "989111" })).toBe("989111");
  });
});
