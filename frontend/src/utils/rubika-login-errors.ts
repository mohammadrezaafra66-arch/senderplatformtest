const OTP_MESSAGES_FA: Record<string, string> = {
  OTP_RATE_LIMITED: "ارسال مجدد کد فعلاً ممکن نیست. لطفاً تا پایان زمان انتظار صبر کنید.",
  OTP_ALREADY_PENDING: "کد قبلاً ارسال شده و هنوز معتبر است. تا پایان زمان انتظار صبر کنید.",
  OTP_INVALID: "کد واردشده نادرست است.",
  OTP_EXPIRED: "کد منقضی شده است. یک کد جدید درخواست کنید.",
  LOGIN_FAILED: "ورود ناموفق بود. دوباره تلاش کنید.",
  LOGIN_REQUIRED: "نیاز به ورود",
  OTP_WAITING: "در انتظار کد",
  OTP_REQUEST_FAILED: "درخواست کد ناموفق بود. کمی بعد دوباره تلاش کنید.",
  SESSION_INVALIDATED: "نیاز به ورود مجدد",
};

export function rubikaLoginErrorMessage(code: string | null | undefined, fallback?: string | null): string {
  if (code && OTP_MESSAGES_FA[code]) return OTP_MESSAGES_FA[code];
  const text = (fallback || "").trim();
  if (text && OTP_MESSAGES_FA[text]) return OTP_MESSAGES_FA[text];
  if (text && !looksInternal(text)) return text;
  return "عملیات ورود انجام نشد. دوباره تلاش کنید.";
}

function looksInternal(text: string): boolean {
  const lowered = text.toLowerCase();
  return (
    lowered.includes("traceback") ||
    lowered.includes("exception") ||
    lowered.includes("sqlalchemy") ||
    /^[A-Z0-9_]+$/.test(text)
  );
}

export function formatResendCountdown(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  const rest = safe % 60;
  if (minutes <= 0) return `${rest} ثانیه`;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}
