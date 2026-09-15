export const DELIVERY_UNKNOWN = "تحویل نامشخص";
export const READ_UNKNOWN = "خوانده‌شدن نامشخص";

const LABELS: Record<string, string> = {
  pending: "آماده",
  queued: "در صف",
  processing: "در حال ارسال",
  accepted_by_worker: "در حال ارسال",
  accepted_by_platform: "ثبت در پلتفرم",
  delivered: "تحویل‌شده",
  read: "خوانده‌شده",
  failed_retryable: "ناموفق موقت",
  failed_permanent: "ناموفق دائم",
  unknown_external_result: "نتیجه ارسال نامشخص",
  dry_run: "آزمایشی",
  shadow_sent: "ارسال سایه",
};

export function sendStatusLabel(status: string | null | undefined): string {
  const key = (status || "").trim().toLowerCase();
  return LABELS[key] || key || "نامشخص";
}

export function claimsDelivery(status: string | null | undefined): boolean {
  return (status || "").trim().toLowerCase() === "delivered";
}

export function claimsRead(status: string | null | undefined): boolean {
  return (status || "").trim().toLowerCase() === "read";
}

export function deliveryNote(status: string | null | undefined): string {
  if (claimsDelivery(status) || claimsRead(status)) return "";
  return DELIVERY_UNKNOWN;
}

export function readNote(status: string | null | undefined): string {
  if (claimsRead(status)) return "";
  return READ_UNKNOWN;
}
