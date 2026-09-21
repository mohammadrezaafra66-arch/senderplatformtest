import { describe, expect, it } from "vitest";

import {
  isActivationReady,
  needsManagerActivationCard,
} from "@/components/RubikaActivationCard";
import type { RubikaActivationStatusResult } from "@/types/rubika";

function row(
  overrides: Partial<RubikaActivationStatusResult>,
): RubikaActivationStatusResult {
  return {
    account_id: 1,
    send_activation_state: "ACTIVATION_PENDING",
    ...overrides,
  };
}

describe("Rubika activation card visibility", () => {
  it("hides the card when there is no activation row", () => {
    expect(
      needsManagerActivationCard(
        row({ status: null, send_activation_state: "ACTIVATION_PENDING" }),
      ),
    ).toBe(false);
  });

  it("shows the card for challenge_sent and pending", () => {
    expect(
      needsManagerActivationCard(row({ status: "challenge_sent" })),
    ).toBe(true);
    expect(needsManagerActivationCard(row({ status: "pending" }))).toBe(true);
  });

  it("shows ready_to_send and hides the confirm card", () => {
    const ready = row({
      send_activation_state: "READY_TO_SEND",
      status: "confirmed",
    });
    expect(isActivationReady(ready)).toBe(true);
    expect(needsManagerActivationCard(ready)).toBe(false);
  });
});
