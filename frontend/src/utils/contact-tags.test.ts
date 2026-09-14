import { describe, expect, it } from "vitest";

import fa from "../../locales/fa/common.json";
import {
  formatAudienceCount,
  parseTagInput,
  previewRowTags,
  recognizedTagColumn,
  tagAudienceError,
  uniqueContactTags,
} from "./contact-tags";

describe("contact tag UI contract", () => {
  it("renders unique tags and does not duplicate chips", () => {
    expect(uniqueContactTags([" طلایی ", "طلایی", "تهران", ""])).toEqual(["طلایی", "تهران"]);
  });

  it("parses the documented tag delimiters for filters and import cells", () => {
    expect(parseTagInput("طلایی، تهران;سرخکن|طلایی")).toEqual(["طلایی", "تهران", "سرخکن"]);
  });

  it("blocks an empty tag audience and accepts ANY and ALL", () => {
    expect(tagAudienceError([], "any")).toBe("حداقل یک برچسب انتخاب کنید.");
    expect(tagAudienceError(["   "], "all")).toBe("حداقل یک برچسب انتخاب کنید.");
    expect(tagAudienceError(["طلایی"], "any")).toBeNull();
    expect(tagAudienceError(["طلایی", "تهران"], "all")).toBeNull();
    expect(tagAudienceError(["طلایی"], "xor")).toBe("حالت تطبیق برچسب نامعتبر است.");
  });

  it("formats the same eligible count the campaign form displays", () => {
    expect(formatAudienceCount(3)).toBe("تعداد گیرندگان واجد شرایط: 3");
    expect(formatAudienceCount(null)).toContain("محاسبه نشده");
  });

  it("recognizes the tag column and preview tags", () => {
    expect(recognizedTagColumn({ phone: "شماره", tags: "برچسب" })).toBe("برچسب");
    expect(recognizedTagColumn({ phone: "شماره", tags: "C" })).toBe("C");
    expect(previewRowTags({ tags: [" تهران ", "تهران"] })).toEqual(["تهران"]);
  });

  it("uses clear Persian labels", () => {
    expect(fa.tags).toBe("برچسب‌ها");
    expect(fa.tagFilter).toBe("فیلتر برچسب");
    expect(fa.tagMatchAny).toBe("حداقل یکی از برچسب‌ها (OR)");
    expect(fa.tagMatchAll).toBe("همه برچسب‌ها (AND)");
    expect(fa.audienceSourceTags).toBe("بر اساس برچسب");
    expect(fa.tagColumnRecognized).toContain("برچسب");
    expect(fa.tagEditHint).toContain("جایگزین");
    expect(fa.tagImportAdditiveHint).toContain("اضافه");
  });
});
