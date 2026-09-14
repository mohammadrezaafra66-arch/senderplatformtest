import { describe, expect, it } from "vitest";

import fa from "../../locales/fa/common.json";
import {
  ADVERTISING_TAG,
  SEND_TIME_PRICE_NOTICE,
  advertisingPriceLabel,
  advertisingReasonLabel,
  canSelectAdvertisingProduct,
  type AdvertisingProductItem,
} from "./advertising-products";

const eligible: AdvertisingProductItem = {
  external_id: "a",
  name: "محصول الف",
  cash_prepayment_price: "10000000",
  advertising_tag: "تبلیغات",
  eligible: true,
  availability: "available",
};

describe("advertising product UI contract", () => {
  it("renders only backend-eligible products with the exact advertising tag", () => {
    expect(ADVERTISING_TAG).toBe("تبلیغات");
    expect(canSelectAdvertisingProduct(eligible)).toBe(true);
    expect(canSelectAdvertisingProduct({ ...eligible, advertising_tag: "تبلیغاتی" })).toBe(false);
  });

  it("blocks unavailable products and products without cash price", () => {
    expect(canSelectAdvertisingProduct({ ...eligible, eligible: false, reason: "PRODUCT_UNAVAILABLE" })).toBe(false);
    expect(advertisingReasonLabel("PRODUCT_UNAVAILABLE")).toBe("ناموجود");
    expect(
      canSelectAdvertisingProduct({
        ...eligible,
        eligible: false,
        cash_prepayment_price: null,
        reason: "CASH_PREPAYMENT_PRICE_MISSING",
      }),
    ).toBe(false);
    expect(advertisingReasonLabel("CASH_PREPAYMENT_PRICE_MISSING")).toBe("فاقد قیمت نقدی (پیش واریز)");
  });

  it("shows the cash/prepayment price and Persian labels", () => {
    expect(advertisingPriceLabel(eligible)).toContain("قیمت نقدی (پیش واریز)");
    expect(advertisingPriceLabel(eligible)).toContain("10000000");
    expect(fa.advertisingTagExact).toBe("تبلیغات");
    expect(fa.cashPrepaymentPrice).toBe("قیمت نقدی (پیش واریز)");
    expect(fa.productUnavailable).toBe("ناموجود");
    expect(SEND_TIME_PRICE_NOTICE).toBe("قیمت محصول هنگام ارسال مجدداً از افراکالا بررسی می‌شود.");
    expect(fa.productPriceRecheckedAtSend).toBe(SEND_TIME_PRICE_NOTICE);
  });
});
