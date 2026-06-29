export function getFailureReasonFa(
  sendStatus: string,
  failureReason?: string | null
): string | null {
  if (!sendStatus) return null;

  const status = sendStatus.toUpperCase();

  if (["DELIVERED", "READ", "PENDING", "QUEUED", "PROCESSING"].includes(status)) {
    return null;
  }

  // بررسی متن error_message از worker
  if (failureReason) {
    if (failureReason.includes("proxy") && failureReason.includes("volume")) {
      return "حجم Proxy این اکانت تمام شده است";
    }
    if (failureReason.includes("proxy") || failureReason.includes("no proxy")) {
      return "Proxy به این اکانت متصل نیست";
    }
    if (failureReason.includes("session_missing") || failureReason.includes("not_linked")) {
      return "واتساپ متصل نیست — لطفاً QR را اسکن کنید";
    }
    if (failureReason.includes("rate_limited") || failureReason.includes("throttled")) {
      return "سرعت ارسال بیش از حد مجاز است";
    }
    if (failureReason.includes("hourly_cap")) {
      return "سقف ارسال ساعتی به پایان رسیده است";
    }
    if (failureReason.includes("recipient_invalid") || failureReason.includes("not_registered")) {
      return "شماره گیرنده در واتساپ ثبت نشده است";
    }
    if (failureReason.includes("unauthorized") || failureReason.includes("Unauthorized")) {
      return "اکانت واتساپ محدود یا بلاک شده است";
    }
    if (failureReason.includes("reengagement")) {
      return "گیرنده نیاز به تعامل مجدد دارد";
    }
  }

  // fallback بر اساس send_status
  switch (status) {
    case "FAILED_PERMANENT":
      return "ارسال با خطای دائمی مواجه شد";
    case "FAILED_RETRYABLE":
      return "ارسال موقتاً ناموفق بود — تلاش مجدد خواهد شد";
    case "OPTED_OUT":
      return "گیرنده از دریافت پیام انصراف داده است";
    case "BLACKLISTED":
      return "گیرنده در لیست سیاه قرار دارد";
    default:
      return failureReason || "خطای ناشناخته";
  }
}
