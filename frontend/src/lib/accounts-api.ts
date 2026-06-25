import { apiFetch } from "@/lib/api";
import type {
  AccountCreatePayload,
  AccountItem,
  AccountsListResult,
  AccountSessionRegisterResult,
  AccountSessionStatus,
  AccountSendTestResult,
  AccountTestConnectionResult,
  AccountUpdatePayload,
  EvolutionInstanceStatus,
  EvolutionQrLinkSession,
  LiveSendPreflight,
  ProxyAssignRequest,
  OperationalSendCapabilities,
  PlatformOption,
  WhatsAppWebLinkSession,
  WhatsAppWebPoolStatus,
  WhatsAppWebRegisterResult,
  WhatsAppWebStatus,
} from "@/types/account";

export async function fetchAccounts(platform?: PlatformOption): Promise<AccountsListResult> {
  const search = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  const response = await apiFetch(`/accounts${search}`);
  return response.json() as Promise<AccountsListResult>;
}

export async function createAccount(
  payload: AccountCreatePayload,
): Promise<{ status: string; account_id: number; message: string }> {
  const response = await apiFetch("/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<{ status: string; account_id: number; message: string }>;
}

export async function updateAccount(
  accountId: number,
  payload: AccountUpdatePayload,
): Promise<AccountItem> {
  const response = await apiFetch(`/accounts/${accountId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<AccountItem>;
}

export async function testAccountConnection(
  accountId: number,
): Promise<AccountTestConnectionResult> {
  const response = await apiFetch(`/accounts/${accountId}/test-connection`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  return response.json() as Promise<AccountTestConnectionResult>;
}

export async function fetchWhatsAppWebStatus(accountId: number): Promise<WhatsAppWebStatus> {
  const response = await apiFetch(`/accounts/${accountId}/whatsapp-web/status`);
  return response.json() as Promise<WhatsAppWebStatus>;
}

export async function registerWhatsAppWebSession(
  accountId: number,
  payload: { linked: boolean; phone?: string | null },
): Promise<WhatsAppWebRegisterResult> {
  const response = await apiFetch(`/accounts/${accountId}/whatsapp-web/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<WhatsAppWebRegisterResult>;
}

export async function startWhatsAppQrLink(accountId: number): Promise<WhatsAppWebLinkSession> {
  const response = await apiFetch(`/accounts/${accountId}/whatsapp-web/link-session`, {
    method: "POST",
  });
  return response.json() as Promise<WhatsAppWebLinkSession>;
}

export async function fetchWhatsAppQrLinkStatus(accountId: number): Promise<WhatsAppWebLinkSession> {
  const response = await apiFetch(`/accounts/${accountId}/whatsapp-web/link-session`);
  return response.json() as Promise<WhatsAppWebLinkSession>;
}

export async function fetchWhatsAppWebPoolStatus(): Promise<WhatsAppWebPoolStatus> {
  const response = await apiFetch("/accounts/whatsapp-web/pool-status");
  return response.json() as Promise<WhatsAppWebPoolStatus>;
}

export async function fetchAccountSessionStatus(accountId: number): Promise<AccountSessionStatus> {
  const response = await apiFetch(`/accounts/${accountId}/session/status`);
  return response.json() as Promise<AccountSessionStatus>;
}

export async function registerAccountSession(
  accountId: number,
  payload: { session_payload: string },
): Promise<AccountSessionRegisterResult> {
  const response = await apiFetch(`/accounts/${accountId}/session/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<AccountSessionRegisterResult>;
}

export async function fetchDeployReadiness(): Promise<Record<string, unknown>> {
  const response = await apiFetch("/accounts/deploy/readiness");
  return response.json() as Promise<Record<string, unknown>>;
}

export async function fetchOperationalSendCapabilities(): Promise<OperationalSendCapabilities> {
  const response = await apiFetch("/accounts/operational-send/capabilities");
  return response.json() as Promise<OperationalSendCapabilities>;
}

export async function fetchLiveSendPreflight(accountId: number): Promise<LiveSendPreflight> {
  const response = await apiFetch(`/accounts/${accountId}/operational-send/preflight`);
  return response.json() as Promise<LiveSendPreflight>;
}

export async function sendAccountTestMessage(
  accountId: number,
  payload: {
    message_text: string;
    recipient?: string | null;
    dry_run?: boolean;
    confirm_live_send?: boolean;
  },
): Promise<AccountSendTestResult> {
  const response = await apiFetch(`/accounts/${accountId}/send-test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dry_run: true,
      confirm_live_send: false,
      ...payload,
    }),
  });
  return response.json() as Promise<AccountSendTestResult>;
}

export async function fetchEvolutionInstanceStatus(
  accountId: number
): Promise<EvolutionInstanceStatus> {
  const response = await apiFetch(
    `/whatsapp/evolution/instance/${accountId}/status`
  );
  return response.json();
}

export async function startEvolutionQrLink(
  accountId: number
): Promise<EvolutionQrLinkSession> {
  const response = await apiFetch(
    `/whatsapp/evolution/instance/${accountId}/connect`,
    { method: "POST" }
  );
  return response.json();
}

export async function disconnectEvolutionInstance(
  accountId: number
): Promise<{ success: boolean; message: string }> {
  const response = await apiFetch(
    `/whatsapp/evolution/instance/${accountId}/logout`,
    { method: "POST" }
  );
  return response.json();
}

export async function assignAccountProxy(
  accountId: number,
  proxy: ProxyAssignRequest
): Promise<{ success: boolean; account_id: number; proxy_assigned: boolean }> {
  const response = await apiFetch(`/whatsapp/evolution/instance/${accountId}/proxy/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(proxy),
  });
  return response.json();
}
