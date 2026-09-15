import { describe, expect, it } from "vitest";

import {
  claimsDelivery,
  claimsRead,
  deliveryNote,
  readNote,
  sendStatusLabel,
} from "./message-status";

describe("message status labels", () => {
  it("does not call platform acceptance delivered or read", () => {
    expect(sendStatusLabel("accepted_by_platform")).toBe("ثبت در پلتفرم");
    expect(claimsDelivery("accepted_by_platform")).toBe(false);
    expect(claimsRead("accepted_by_platform")).toBe(false);
    expect(deliveryNote("accepted_by_platform")).toBe("تحویل نامشخص");
    expect(readNote("accepted_by_platform")).toBe("خوانده‌شدن نامشخص");
  });

  it("distinguishes retryable, permanent, and unknown results", () => {
    expect(sendStatusLabel("failed_retryable")).toBe("ناموفق موقت");
    expect(sendStatusLabel("failed_permanent")).toBe("ناموفق دائم");
    expect(sendStatusLabel("unknown_external_result")).toBe("نتیجه ارسال نامشخص");
    expect(sendStatusLabel("queued")).toBe("در صف");
  });
});
