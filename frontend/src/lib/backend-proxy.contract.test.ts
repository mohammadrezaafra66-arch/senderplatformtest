import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("Next /backend proxy rewrite contract", () => {
  it("rewrites /backend/:path* without transforming method or body", () => {
    const raw = readFileSync(resolve(__dirname, "../../next.config.ts"), "utf8");
    expect(raw).toContain('source: "/backend/:path*"');
    expect(raw).toContain("destination:");
    expect(raw).toContain("MMP_API_PROXY_TARGET");
    // Rewrites are reverse-proxy style; no body middleware in this file.
    expect(raw).not.toMatch(/bodyParser|stripBody|removeBody/i);
  });
});
