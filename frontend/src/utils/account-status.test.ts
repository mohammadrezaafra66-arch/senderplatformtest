import { describe, expect, it } from "vitest";

import { runtimeStatusLabel } from "./account-status";

describe("rubika operator readiness labels", () => {
  it("distinguishes login, worker, and stop states", () => {
    expect(runtimeStatusLabel("LOGIN_REQUIRED")).toBe("نیاز به ورود");
    expect(runtimeStatusLabel("AUTHENTICATED_NO_WORKER")).toBe("احراز شده، Worker آماده نیست");
    expect(runtimeStatusLabel("READY")).toBe("آماده ارسال");
    expect(runtimeStatusLabel("AUTHENTICATED_NO_WORKER", undefined, "Worker قطع شده")).toBe(
      "Worker قطع شده",
    );
    expect(runtimeStatusLabel(null, undefined, null, "WORKER_COVERED_NOT_DISPATCH")).toBe(
      "Worker آماده",
    );
    expect(runtimeStatusLabel(null, undefined, null, "ACCOUNT_BANNED")).toBe("مسدود");
    expect(runtimeStatusLabel(null, undefined, null, "ACCOUNT_RESTING")).toBe("متوقف");
    expect(runtimeStatusLabel(null, undefined, null, "QUARANTINED")).toBe("قرنطینه");
  });
});
