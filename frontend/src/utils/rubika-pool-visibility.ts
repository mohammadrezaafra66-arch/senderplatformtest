export type RubikaPoolVisibilityFields = {
  archived?: boolean | null;
  archived_at?: string | null;
};

export function isRubikaPoolAccountArchived(
  item: RubikaPoolVisibilityFields | null | undefined,
): boolean {
  if (!item) return false;
  if (item.archived === true) return true;
  if (item.archived_at != null && String(item.archived_at).trim() !== "") {
    return true;
  }
  return false;
}

export function visibleRubikaPoolAccounts<T extends RubikaPoolVisibilityFields>(
  items: T[] | null | undefined,
): T[] {
  return (items ?? []).filter((item) => !isRubikaPoolAccountArchived(item));
}

export function paginateRubikaPoolAccounts<T extends RubikaPoolVisibilityFields>(
  items: T[] | null | undefined,
  offset = 0,
  limit?: number,
): { items: T[]; totalCount: number } {
  const visible = visibleRubikaPoolAccounts(items);
  const start = Math.max(0, offset);
  const page =
    limit == null ? visible.slice(start) : visible.slice(start, start + Math.max(0, limit));
  return { items: page, totalCount: visible.length };
}
