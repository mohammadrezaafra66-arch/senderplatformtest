export type AdvertisingProductItem = {
  external_id: string;
  name: string;
  cash_prepayment_price?: string | null;
  currency?: string | null;
  advertising_tag?: string | null;
  eligible: boolean;
  reason?: string | null;
  availability?: string | null;
};

export const ADVERTISING_TAG = "تبلیغات";
export const CASH_PRICE_LABEL = "قیمت نقدی (پیش واریز)";
export const SEND_TIME_PRICE_NOTICE = "قیمت محصول هنگام ارسال مجدداً از افراکالا بررسی می‌شود.";

const REASON_LABELS: Record<string, string> = {
  MISSING_ADVERTISING_TAG: "فاقد تگ تبلیغات",
  PRODUCT_UNAVAILABLE: "ناموجود",
  CASH_PREPAYMENT_PRICE_MISSING: "فاقد قیمت نقدی (پیش واریز)",
  INVALID_PRICE: "قیمت نقدی (پیش واریز) نامعتبر است",
  INVALID_PRODUCT_DATA: "اطلاعات محصول ناقص است",
  unavailable: "ناموجود",
  unknown: "موجودی نامشخص است",
};

export function advertisingReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  return REASON_LABELS[reason] || "این محصول قابل انتخاب نیست";
}

export function canSelectAdvertisingProduct(item: AdvertisingProductItem): boolean {
  return item.eligible === true && item.advertising_tag === ADVERTISING_TAG && Boolean(item.cash_prepayment_price);
}

export function advertisingPriceLabel(item: AdvertisingProductItem): string {
  if (!item.cash_prepayment_price) return "فاقد قیمت نقدی (پیش واریز)";
  return `${CASH_PRICE_LABEL}: ${item.cash_prepayment_price}`;
}
