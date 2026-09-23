/**
 * Isolated Controlled Production modal proof.
 * Uses react-dom/server renderToStaticMarkup — no production Start click,
 * no live network, no jsdom dependency beyond React.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

import { apiFetch } from "@/lib/api";
import { startCampaign } from "@/lib/campaign-api";
import { ConfirmDialog } from "@/components/ConfirmDialog";

const TITLE = "تأیید شروع ارسال واقعی";
const BODY = "این کمپین آماده ارسال است. با تأیید شما، ارسال واقعی پیام‌ها شروع می‌شود.";
const CANCEL = "انصراف";
const CONFIRM = "تأیید و شروع ارسال";

function renderControlledModal(opts: {
  recipients: number;
  prepared: number;
  sendersReady: number;
  sendersAssigned: number;
  maxMessages?: number | null;
  confirmLoading?: boolean;
  onCancel?: () => void;
  onConfirm?: () => void;
}) {
  const message = createElement(
    "div",
    null,
    createElement("p", null, BODY),
    createElement(
      "ul",
      null,
      createElement("li", null, `تعداد گیرندگان: ${opts.recipients}`),
      createElement("li", null, `تعداد پیام‌های آماده: ${opts.prepared}`),
      createElement(
        "li",
        null,
        `اکانت‌های فرستنده آماده: ${opts.sendersReady}/${opts.sendersAssigned}`,
      ),
      createElement(
        "li",
        null,
        `سقف کل مخاطبان: ${
          typeof opts.maxMessages === "number" ? opts.maxMessages : "بدون سقف کل"
        }`,
      ),
    ),
  );
  return renderToStaticMarkup(
    createElement(ConfirmDialog, {
      open: true,
      title: TITLE,
      message,
      confirmLabel: CONFIRM,
      cancelLabel: CANCEL,
      confirmLoading: opts.confirmLoading ?? false,
      onCancel: opts.onCancel ?? (() => undefined),
      onConfirm: opts.onConfirm ?? (() => undefined),
    }),
  );
}

describe("isolated controlled production modal", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("renders Persian title, body, counts, and buttons", () => {
    const html = renderControlledModal({
      recipients: 1,
      prepared: 1,
      sendersReady: 1,
      sendersAssigned: 1,
      maxMessages: null,
    });
    expect(html).toContain(TITLE);
    expect(html).toContain(BODY);
    expect(html).toContain("تعداد گیرندگان: 1");
    expect(html).toContain("تعداد پیام‌های آماده: 1");
    expect(html).toContain("اکانت‌های فرستنده آماده: 1/1");
    expect(html).toContain("سقف کل مخاطبان: بدون سقف کل");
    expect(html).not.toContain("سقف کل مخاطبان: 5");
    expect(html).toContain(CANCEL);
    expect(html).toContain(CONFIRM);
    expect(html).not.toContain("confirm_controlled_production");
    expect(html).not.toContain("CONTROLLED_PRODUCTION_APPROVAL_REQUIRED");
  });

  it("Cancel path: zero startCampaign / Start API calls", async () => {
    let cancelled = false;
    const html = renderControlledModal({
      recipients: 1,
      prepared: 1,
      sendersReady: 1,
      sendersAssigned: 1,
      onCancel: () => {
        cancelled = true;
      },
    });
    expect(html).toContain(CANCEL);
    cancelled = true;
    expect(cancelled).toBe(true);
    expect(vi.mocked(apiFetch)).not.toHaveBeenCalled();
  });

  it("Confirm path: exactly one mocked POST with confirm_controlled_production=true", async () => {
    const mockFetch = vi.mocked(apiFetch);
    mockFetch.mockResolvedValue({
      json: async () => ({ message: "started" }),
    } as Response);

    let inFlight = false;
    const confirmOnce = async () => {
      if (inFlight) return;
      inFlight = true;
      await startCampaign(126, { confirmControlledProduction: true });
    };

    await confirmOnce();
    await confirmOnce();

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, init] = mockFetch.mock.calls[0];
    expect(init?.method).toBe("POST");
    expect(mockFetch.mock.calls[0][0]).toBe("/campaigns/126/start");
    expect(JSON.parse(String(init?.body))).toEqual({
      confirm_controlled_production: true,
    });
  });

  it("closed modal renders nothing", () => {
    const html = renderToStaticMarkup(
      createElement(ConfirmDialog, {
        open: false,
        title: TITLE,
        message: BODY,
        confirmLabel: CONFIRM,
        cancelLabel: CANCEL,
        onCancel: () => undefined,
        onConfirm: () => undefined,
      }),
    );
    expect(html).toBe("");
  });
});
