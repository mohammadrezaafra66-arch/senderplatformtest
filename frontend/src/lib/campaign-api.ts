import { apiFetch } from "@/lib/api";
import type { ArchiveActionResult } from "@/types/account";
import type {
  CampaignDetail,
  CampaignAccountsResult,
  CampaignListItem,
  CampaignPreflight,
  CampaignRecipientItem,
  CampaignRenderPreview,
  CreateCampaignFromImportPayload,
  CreateCampaignFromContactsPayload,
  CreateCampaignFromTagsPayload,
  AudiencePreviewResult,
  MessageLogDetail,
} from "@/types/campaign";

export type CampaignsListResult = {
  items: CampaignListItem[];
  total_count: number;
  limit: number;
  offset: number;
};

export async function fetchCampaigns(params: {
  limit?: number;
  offset?: number;
  status?: string;
  archived?: boolean;
  q?: string;
  token?: string | null;
}): Promise<CampaignsListResult> {
  const search = new URLSearchParams();
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));
  if (params.status) search.set("status", params.status);
  if (params.archived) search.set("archived", "true");
  if (params.q?.trim()) search.set("q", params.q.trim());

  const response = await apiFetch(`/campaigns?${search.toString()}`, {
    token: params.token,
  });
  return response.json() as Promise<CampaignsListResult>;
}

export async function archiveCampaign(
  id: number,
  options: { token?: string | null; reason?: string } = {},
): Promise<ArchiveActionResult> {
  const body = options.reason ? JSON.stringify({ reason: options.reason }) : undefined;
  const response = await apiFetch(`/campaigns/${id}/archive`, {
    method: "POST",
    token: options.token,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body,
  });
  return response.json() as Promise<ArchiveActionResult>;
}

export async function restoreCampaign(
  id: number,
  token?: string | null,
): Promise<ArchiveActionResult> {
  const response = await apiFetch(`/campaigns/${id}/restore`, {
    method: "POST",
    token,
  });
  return response.json() as Promise<ArchiveActionResult>;
}

export async function fetchCampaignDetail(campaignId: number): Promise<CampaignDetail> {
  const response = await apiFetch(`/campaigns/${campaignId}`);
  return response.json() as Promise<CampaignDetail>;
}

export async function fetchCampaignPreflight(campaignId: number): Promise<CampaignPreflight> {
  const response = await apiFetch(`/campaigns/${campaignId}/preflight`);
  return response.json() as Promise<CampaignPreflight>;
}

export type CampaignStartResult = {
  status: string;
  campaign_id: number;
  message: string;
  bridge_result?: Record<string, number | string> | null;
  preflight?: Record<string, unknown> | null;
  request_id?: string | null;
  accepted?: boolean;
  campaign_status?: string | null;
  queue_jobs_created?: number | null;
  messages_scheduled?: number | null;
  controlled_confirmation_accepted?: boolean;
};

function newStartRequestId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `start-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export async function startCampaign(
  campaignId: number,
  options: { confirmControlledProduction?: boolean } = {},
): Promise<CampaignStartResult> {
  const confirm = Boolean(options.confirmControlledProduction);
  const body = { confirm_controlled_production: confirm };
  const requestId = newStartRequestId();
  const response = await apiFetch(`/campaigns/${campaignId}/start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Request-Id": requestId,
    },
    body: JSON.stringify(body),
  });
  return response.json() as Promise<CampaignStartResult>;
}

export async function stopCampaign(campaignId: number): Promise<{ message: string }> {
  const response = await apiFetch(`/campaigns/${campaignId}/stop`, { method: "POST" });
  return response.json() as Promise<{ message: string }>;
}

export type CampaignPrepareResult = {
  campaign_id: number;
  staged_count: number;
  ready_count: number;
  already_staged_count: number;
  real_gpt_called: boolean;
  message: string;
};

export async function prepareCampaign(
  campaignId: number,
  options: { force_mock_output?: boolean } = {},
): Promise<CampaignPrepareResult> {
  const response = await apiFetch(`/campaigns/${campaignId}/prepare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  return response.json() as Promise<CampaignPrepareResult>;
}

export async function fetchCampaignRecipients(
  campaignId: number,
  params: { limit?: number; offset?: number; send_status?: string } = {},
): Promise<{ items: CampaignRecipientItem[]; total_count: number }> {
  const search = new URLSearchParams();
  search.set("limit", String(params.limit ?? 50));
  search.set("offset", String(params.offset ?? 0));
  if (params.send_status) search.set("send_status", params.send_status);

  const response = await apiFetch(`/campaigns/${campaignId}/recipients?${search.toString()}`);
  const data = (await response.json()) as {
    items: CampaignRecipientItem[];
    total_count: number;
  };
  return data;
}

export async function fetchCampaignRecipientDetail(
  campaignId: number,
  recipientId: number,
): Promise<MessageLogDetail> {
  const response = await apiFetch(`/campaigns/${campaignId}/recipients/${recipientId}`);
  return response.json() as Promise<MessageLogDetail>;
}

export async function downloadCampaignRecipientsExport(
  campaignId: number,
  sendStatus?: string,
): Promise<void> {
  const search = new URLSearchParams();
  if (sendStatus) search.set("send_status", sendStatus);

  const query = search.toString();
  const path = `/campaigns/${campaignId}/recipients/export${query ? `?${query}` : ""}`;
  const response = await apiFetch(path);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `campaign_${campaignId}_recipients.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export async function createCampaignFromImport(
  payload: CreateCampaignFromImportPayload,
): Promise<{ campaign_id: number; message: string }> {
  const response = await apiFetch("/campaigns/from-import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<{ campaign_id: number; message: string }>;
}

export async function createCampaignFromContacts(
  payload: CreateCampaignFromContactsPayload,
): Promise<{ campaign_id: number; message: string }> {
  const response = await apiFetch("/campaigns/from-contacts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<{ campaign_id: number; message: string }>;
}

export async function previewTagAudience(payload: {
  selected_tags: string[];
  tag_match: "any" | "all";
}): Promise<AudiencePreviewResult> {
  const response = await apiFetch("/campaigns/audience-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<AudiencePreviewResult>;
}

export async function createCampaignFromTags(
  payload: CreateCampaignFromTagsPayload,
): Promise<{ campaign_id: number; contacts_attached_count: number; message: string }> {
  const response = await apiFetch("/campaigns/from-tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<{
    campaign_id: number;
    contacts_attached_count: number;
    message: string;
  }>;
}

export async function updateCampaignAccounts(
  campaignId: number,
  accountIds: number[],
): Promise<CampaignAccountsResult> {
  const response = await apiFetch(`/campaigns/${campaignId}/accounts`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ account_ids: accountIds }),
  });
  return response.json() as Promise<CampaignAccountsResult>;
}

export type ProductFeedStatus = {
  ok: boolean;
  code: string;
  eligible_count: number;
  fetched_at: string | null;
  source_updated_at: string | null;
  message: string;
  live_binding?: string;
};

export async function fetchProductFeedStatus(): Promise<ProductFeedStatus> {
  const response = await apiFetch("/campaigns/product-feed/status");
  return response.json() as Promise<ProductFeedStatus>;
}

export type GptStatus = {
  ok: boolean;
  configured: boolean;
  live_binding?: string;
  message: string;
};

export type GptPreviewSample = {
  variation_id: string;
  label: string;
  prose_text: string;
  immutable_product_block: string;
  final_text: string;
  heading: string;
};

export type GptPreviewResult = {
  ok: boolean;
  code?: string | null;
  configured: boolean;
  live_binding?: string;
  provider?: string;
  samples: GptPreviewSample[];
  product_preview_note?: string | null;
  product_error?: { code?: string; message?: string } | null;
  message: string;
};

export async function fetchGptStatus(): Promise<GptStatus> {
  const response = await apiFetch("/campaigns/gpt-status");
  return response.json() as Promise<GptStatus>;
}

export async function previewGptVariations(payload: {
  template_text: string;
  include_products: boolean;
  requested_count?: number;
}): Promise<GptPreviewResult> {
  const response = await apiFetch("/campaigns/gpt-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<GptPreviewResult>;
}

export async function previewCampaignRender(payload: {
  template_text: string;
  platform?: string;
  use_gpt: boolean;
  include_products: boolean;
  preview_count?: number;
}): Promise<CampaignRenderPreview> {
  const response = await apiFetch("/campaigns/render-preview", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return response.json() as Promise<CampaignRenderPreview>;
}
