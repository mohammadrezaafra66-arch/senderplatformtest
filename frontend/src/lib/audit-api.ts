import { apiFetch } from "@/lib/api";
import type { AuditLogListResult } from "@/types/audit";

export async function fetchAuditLogs(limit = 50): Promise<AuditLogListResult> {
  const response = await apiFetch(`/audit/logs?limit=${limit}`);
  return response.json() as Promise<AuditLogListResult>;
}
