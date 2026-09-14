export type TagMatch = "any" | "all";

const TAG_SPLIT = /[,،;|]+/;

export function uniqueContactTags(tags: Array<string | null | undefined> | null | undefined): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of tags ?? []) {
    const tag = String(raw ?? "")
      .replace(/\u200c/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    out.push(tag);
  }
  return out;
}

export function parseTagInput(value: string): string[] {
  return uniqueContactTags(value.split(TAG_SPLIT));
}

export function tagAudienceError(tags: string[], match: string): string | null {
  if (uniqueContactTags(tags).length === 0) {
    return "حداقل یک برچسب انتخاب کنید.";
  }
  if (match !== "any" && match !== "all") {
    return "حالت تطبیق برچسب نامعتبر است.";
  }
  return null;
}

export function formatAudienceCount(count: number | null): string {
  if (count == null) return "تعداد گیرندگان هنوز محاسبه نشده است.";
  return `تعداد گیرندگان واجد شرایط: ${count}`;
}

export function recognizedTagColumn(mapping: Record<string, string> | null | undefined): string | null {
  const column = mapping?.tags;
  return column?.trim() ? column : null;
}

export function previewRowTags(normalized: { tags?: string[] | null } | null | undefined): string[] {
  return uniqueContactTags(normalized?.tags);
}
