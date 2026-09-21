import { describe, expect, it } from "vitest";

import {
  ACCOUNT_MESSAGE_LIMIT_PLACEHOLDER,
  accountMessageLimitToInputValue,
  formatAccountCapRemaining,
  formatDailyCapHeadline,
  formatHourlyCapHeadline,
  parseAccountMessageLimitInput,
} from "./account-message-limits";

describe("account message limit bind/save/clear", () => {
  it("uses an empty input as the unlimited placeholder", () => {
    expect(ACCOUNT_MESSAGE_LIMIT_PLACEHOLDER).toBe("بدون محدودیت");
    expect(accountMessageLimitToInputValue(null)).toBe("");
    expect(accountMessageLimitToInputValue(undefined)).toBe("");
    expect(accountMessageLimitToInputValue(10)).toBe("10");
  });

  it("parses empty input to JSON null instead of 0", () => {
    expect(parseAccountMessageLimitInput("")).toBeNull();
    expect(parseAccountMessageLimitInput("   ")).toBeNull();
  });

  it("parses a positive integer for save", () => {
    expect(parseAccountMessageLimitInput("12")).toBe(12);
  });

  it("rejects 0, negative, float, and non-integer text", () => {
    expect(() => parseAccountMessageLimitInput("0")).toThrow();
    expect(() => parseAccountMessageLimitInput("-1")).toThrow();
    expect(() => parseAccountMessageLimitInput("1.5")).toThrow();
    expect(() => parseAccountMessageLimitInput("abc")).toThrow();
  });
});

describe("unlimited vs redis-unknown display", () => {
  it("shows بدون محدودیت when the dimension is unlimited", () => {
    expect(
      formatAccountCapRemaining({
        unlimited: true,
        remaining: null,
        quotaKnown: false,
      }),
    ).toBe("بدون محدودیت");
    expect(formatHourlyCapHeadline(true)).toBe("سقف ساعتی: بدون محدودیت");
    expect(formatDailyCapHeadline(true)).toBe("سقف روزانه: بدون محدودیت");
  });

  it("shows ظرفیت لحظه‌ای نامشخص when redis quota is unknown", () => {
    expect(
      formatAccountCapRemaining({
        unlimited: false,
        remaining: null,
        quotaKnown: false,
      }),
    ).toBe("ظرفیت لحظه‌ای نامشخص");
  });

  it("shows the remaining count when the cap is known", () => {
    expect(
      formatAccountCapRemaining({
        unlimited: false,
        remaining: 7,
        quotaKnown: true,
      }),
    ).toBe("7");
  });
});
