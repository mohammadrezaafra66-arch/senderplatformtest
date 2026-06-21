export const PLATFORM_OPTIONS = ["whatsapp", "telegram", "bale", "rubika"] as const;

export const ACCOUNT_STATUS_OPTIONS = [
  "active",
  "resting",
  "banned",
  "requires_login",
] as const;

export function accountStatusLabel(status: string, t: (key: string) => string): string {
  const key = `account_status_${status}`;
  const translated = t(key);
  return translated === key ? status : translated;
}

export function accountStatusColor(status: string): string {
  if (status === "active") return "#166534";
  if (status === "resting") return "#b45309";
  if (status === "banned") return "#991b1b";
  if (status === "requires_login") return "#1d4ed8";
  return "inherit";
}
