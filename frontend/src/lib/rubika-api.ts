import { apiFetch } from "@/lib/api";
import type {
  RubikaAccountsListResult,
  RubikaAlertItem,
  RubikaGroupCreatePayload,
  RubikaGroupItem,
  RubikaGroupMessagesResult,
  RubikaGroupsListResult,
  RubikaGroupUpdatePayload,
  RubikaIncidentItem,
  RubikaPoolPhase,
  RubikaPoolRestoreResult,
  RubikaPoolUpsertResult,
  RubikaProtectionOverview,
  RubikaProtectionRestoreResult,
  RubikaScheduleItem,
  RubikaScheduleListResult,
  RubikaSendLogResult,
  RubikaUserLoginStartResult,
  RubikaUserLoginVerifyResult,
} from "@/types/rubika";

// ─── استخر اکانت‌ها ───

export async function fetchRubikaAccounts(): Promise<RubikaAccountsListResult> {
  const response = await apiFetch("/rubika/accounts");
  return response.json() as Promise<RubikaAccountsListResult>;
}

export async function upsertRubikaPool(
  accountId: number,
  payload: { phase: RubikaPoolPhase; priority: number },
): Promise<RubikaPoolUpsertResult> {
  const response = await apiFetch(`/rubika/accounts/${accountId}/pool`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaPoolUpsertResult>;
}

export async function removeRubikaPoolMembership(
  accountId: number,
  phase: RubikaPoolPhase,
): Promise<{ success: boolean }> {
  const response = await apiFetch(`/rubika/accounts/${accountId}/pool/${phase}`, {
    method: "DELETE",
  });
  return response.json() as Promise<{ success: boolean }>;
}

export async function restoreRubikaAccount(accountId: number): Promise<RubikaPoolRestoreResult> {
  const response = await apiFetch(`/rubika/accounts/${accountId}/pool/restore`, {
    method: "POST",
  });
  return response.json() as Promise<RubikaPoolRestoreResult>;
}

// ─── ورود تعاملی اکانت شخصی (OTP) ───

export async function startRubikaUserLogin(
  accountId: number,
  payload: { phone_number?: string; pass_key?: string; registration_token?: string },
): Promise<RubikaUserLoginStartResult> {
  const response = await apiFetch(`/accounts/${accountId}/rubika/session/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaUserLoginStartResult>;
}

export async function verifyRubikaUserLogin(
  accountId: number,
  payload: { registration_token: string; phone_code: string },
): Promise<RubikaUserLoginVerifyResult> {
  const response = await apiFetch(`/accounts/${accountId}/rubika/session/verify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaUserLoginVerifyResult>;
}

// ─── لاگ ارسال ───

export async function fetchRubikaSendLog(params: {
  limit?: number;
  offset?: number;
  account_id?: number;
}): Promise<RubikaSendLogResult> {
  const search = new URLSearchParams();
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  if (params.account_id != null) search.set("account_id", String(params.account_id));
  const qs = search.toString();
  const response = await apiFetch(`/rubika/send-log${qs ? `?${qs}` : ""}`);
  return response.json() as Promise<RubikaSendLogResult>;
}

// ─── گروه‌ها ───

export async function fetchRubikaGroups(): Promise<RubikaGroupsListResult> {
  const response = await apiFetch("/rubika/groups");
  return response.json() as Promise<RubikaGroupsListResult>;
}

export async function createRubikaGroup(
  payload: RubikaGroupCreatePayload,
): Promise<RubikaGroupItem> {
  const response = await apiFetch("/rubika/groups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaGroupItem>;
}

export async function updateRubikaGroup(
  groupId: number,
  payload: RubikaGroupUpdatePayload,
): Promise<RubikaGroupItem> {
  const response = await apiFetch(`/rubika/groups/${groupId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaGroupItem>;
}

export async function deleteRubikaGroup(groupId: number): Promise<{ success: boolean }> {
  const response = await apiFetch(`/rubika/groups/${groupId}`, { method: "DELETE" });
  return response.json() as Promise<{ success: boolean }>;
}

export async function fetchRubikaGroupMessages(
  groupId: number,
  params: { limit?: number; offset?: number } = {},
): Promise<RubikaGroupMessagesResult> {
  const search = new URLSearchParams();
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const qs = search.toString();
  const response = await apiFetch(`/rubika/groups/${groupId}/messages${qs ? `?${qs}` : ""}`);
  return response.json() as Promise<RubikaGroupMessagesResult>;
}

// ─── زمان‌بندی ───

export async function fetchRubikaSchedule(): Promise<RubikaScheduleListResult> {
  const response = await apiFetch("/rubika/schedule");
  return response.json() as Promise<RubikaScheduleListResult>;
}

export async function updateRubikaSchedule(
  phase: string,
  payload: { start_hour: number; end_hour: number; max_per_hour: number; is_active: boolean },
): Promise<RubikaScheduleItem> {
  const response = await apiFetch(`/rubika/schedule/${phase}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<RubikaScheduleItem>;
}

// ─── دستیار افراکالا (قیمت لحظه‌ای) — endpoint عمومی موجود، مخصوص روبیکا نیست ───

export type RubikaAssistantPricing = {
  success?: boolean;
  cached_at?: string | null;
  [key: string]: unknown;
};

export async function fetchAfrakalaAssistantPricing(): Promise<RubikaAssistantPricing> {
  const response = await apiFetch("/debug/pricing-cache");
  return response.json() as Promise<RubikaAssistantPricing>;
}

export async function refreshAfrakalaAssistantPricing(): Promise<RubikaAssistantPricing> {
  const response = await apiFetch("/debug/pricing-cache/refresh", { method: "POST" });
  return response.json() as Promise<RubikaAssistantPricing>;
}

// ─── Phase 5 — Protection / Operations ───

export async function fetchRubikaProtectionOverview(): Promise<RubikaProtectionOverview> {
  const response = await apiFetch("/rubika/protection/overview");
  return response.json() as Promise<RubikaProtectionOverview>;
}

export async function fetchRubikaProtectionAccountDetail(
  accountId: number,
): Promise<Record<string, unknown>> {
  const response = await apiFetch(`/rubika/accounts/${accountId}/protection`);
  return response.json() as Promise<Record<string, unknown>>;
}

export async function restoreRubikaProtectionAccount(
  accountId: number,
): Promise<RubikaProtectionRestoreResult> {
  const response = await apiFetch(`/rubika/accounts/${accountId}/restore`, {
    method: "POST",
  });
  return response.json() as Promise<RubikaProtectionRestoreResult>;
}

export async function fetchRubikaIncidents(params: {
  status?: string;
  scope?: string;
  severity?: string;
  category?: string;
  account_id?: number;
  sort?: string;
} = {}): Promise<{ items: RubikaIncidentItem[]; count: number }> {
  const search = new URLSearchParams();
  if (params.status) search.set("status", params.status);
  if (params.scope) search.set("scope", params.scope);
  if (params.severity) search.set("severity", params.severity);
  if (params.category) search.set("category", params.category);
  if (params.account_id != null) search.set("account_id", String(params.account_id));
  if (params.sort) search.set("sort", params.sort);
  const qs = search.toString();
  const response = await apiFetch(`/rubika/incidents${qs ? `?${qs}` : ""}`);
  return response.json() as Promise<{ items: RubikaIncidentItem[]; count: number }>;
}

export async function acknowledgeRubikaIncident(
  incidentId: string,
): Promise<{ state: string; incident: RubikaIncidentItem }> {
  const response = await apiFetch(`/rubika/incidents/${incidentId}/acknowledge`, {
    method: "POST",
  });
  return response.json() as Promise<{ state: string; incident: RubikaIncidentItem }>;
}

export async function fetchRubikaAlerts(): Promise<{ items: RubikaAlertItem[]; count: number }> {
  const response = await apiFetch("/rubika/alerts");
  return response.json() as Promise<{ items: RubikaAlertItem[]; count: number }>;
}

export async function acknowledgeRubikaAlert(
  dedupeKey: string,
): Promise<{ state: string; alert: RubikaAlertItem }> {
  const response = await apiFetch(
    `/rubika/alerts/${encodeURIComponent(dedupeKey)}/acknowledge`,
    { method: "POST" },
  );
  return response.json() as Promise<{ state: string; alert: RubikaAlertItem }>;
}
