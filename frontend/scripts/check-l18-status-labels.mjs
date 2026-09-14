/** L18 frontend label map check (no jest). Run: node scripts/check-l18-status-labels.mjs */
import { createRequire } from "module";
import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, "../src/utils/account-status.ts"), "utf8");
const fa = JSON.parse(readFileSync(join(__dirname, "../locales/fa/common.json"), "utf8"));

const required = [
  "DISABLED",
  "LOGIN_REQUIRED",
  "OTP_WAITING",
  "AUTHENTICATING",
  "AUTHENTICATED_NO_WORKER",
  "READY",
  "MANUAL_REVIEW",
  "SESSION_ERROR",
  "CONNECTION_ERROR",
  "CONFIG_ERROR",
  "NOT_APPLICABLE",
];

for (const s of required) {
  const key = `runtime_status_${s}`;
  if (!fa[key]) {
    console.error("MISSING_FA", key);
    process.exit(2);
  }
  if (s === "READY" && fa[key] === "فعال") {
    console.error("READY_MUST_NOT_BE_FAAL");
    process.exit(3);
  }
}
if (!src.includes("isLifecycleConnectedLie")) {
  console.error("MISSING_HELPER");
  process.exit(4);
}
if (fa.runtime_status_READY !== "آماده ارسال") {
  console.error("BAD_READY_LABEL", fa.runtime_status_READY);
  process.exit(5);
}
console.log("FRONTEND_L18_LABEL_CHECK_PASS=True");
