export function campaignStatusLabel(status: string, t: (key: string) => string): string {
  const key = `status_${status}`;
  const translated = t(key);
  return translated === key ? status : translated;
}

export const SEND_STATUS_OPTIONS = [
  "pending",
  "queued",
  "processing",
  "delivered",
  "failed_permanent",
  "failed_retryable",
  "opted_out",
  "blacklisted",
] as const;
