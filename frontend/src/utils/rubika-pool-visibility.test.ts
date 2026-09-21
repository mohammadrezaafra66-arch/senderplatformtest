import { describe, expect, it } from "vitest";

import {
  isRubikaPoolAccountArchived,
  paginateRubikaPoolAccounts,
  visibleRubikaPoolAccounts,
} from "@/utils/rubika-pool-visibility";

type Row = {
  account_id: number;
  archived?: boolean | null;
  archived_at?: string | null;
};

function row(id: number, extra: Partial<Row> = {}): Row {
  return { account_id: id, ...extra };
}

describe("rubika pool archived visibility", () => {
  it("treats archived=true and archived_at as hidden", () => {
    expect(isRubikaPoolAccountArchived(row(1, { archived: true }))).toBe(true);
    expect(
      isRubikaPoolAccountArchived(row(2, { archived_at: "2026-09-21T00:00:00Z" })),
    ).toBe(true);
  });

  it("keeps non-archived accounts visible", () => {
    expect(isRubikaPoolAccountArchived(row(3))).toBe(false);
    expect(isRubikaPoolAccountArchived(row(4, { archived: false, archived_at: null }))).toBe(
      false,
    );
  });

  it("drops archived rows from the table list without changing active ones", () => {
    const items = [
      row(10),
      row(11, { archived: true }),
      row(12, { archived_at: "2026-09-20T12:00:00Z" }),
      row(13, { archived: false }),
    ];
    expect(visibleRubikaPoolAccounts(items).map((item) => item.account_id)).toEqual([
      10, 13,
    ]);
  });

  it("counts and paginates only non-archived accounts", () => {
    const items = [
      row(1, { archived: true }),
      row(2),
      row(3),
      row(4, { archived_at: "2026-09-21T00:00:00Z" }),
      row(5),
    ];
    const page = paginateRubikaPoolAccounts(items, 1, 1);
    expect(page.totalCount).toBe(3);
    expect(page.items.map((item) => item.account_id)).toEqual([3]);
  });
});
