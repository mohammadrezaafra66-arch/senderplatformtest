export const ACCOUNT_MESSAGE_LIMIT_PLACEHOLDER = "بدون محدودیت";

export function parseAccountMessageLimitInput(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  if (!/^[1-9][0-9]*$/.test(trimmed)) {
    throw new Error("invalid_account_message_limit");
  }
  const value = Number(trimmed);
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error("invalid_account_message_limit");
  }
  return value;
}

export function accountMessageLimitToInputValue(
  value: number | null | undefined,
): string {
  if (value == null) return "";
  return String(value);
}

export function formatAccountCapRemaining(options: {
  unlimited?: boolean | null;
  remaining?: number | null;
  quotaKnown?: boolean | null;
  unlimitedLabel?: string;
  unknownLabel?: string;
}): string {
  if (options.unlimited) {
    return options.unlimitedLabel ?? "بدون محدودیت";
  }
  if (options.quotaKnown === false || options.remaining == null) {
    return options.unknownLabel ?? "ظرفیت لحظه‌ای نامشخص";
  }
  return String(options.remaining);
}

export function formatHourlyCapHeadline(unlimited: boolean): string {
  return unlimited ? "سقف ساعتی: بدون محدودیت" : "سقف ساعتی";
}

export function formatDailyCapHeadline(unlimited: boolean): string {
  return unlimited ? "سقف روزانه: بدون محدودیت" : "سقف روزانه";
}
