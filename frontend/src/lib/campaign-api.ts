import { apiFetch } from "@/lib/api";
import type {
  CampaignDetail,
  CampaignListItem,
  CampaignRecipientItem,
  CreateCampaignFromImportPayload,
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
}): Promise<CampaignsListResult> {
  const search = new URLSearchParams();
  search.set("limit", String(params.limit ?? 20));
  search.set("offset", String(params.offset ?? 0));
  if (params.status) search.set("status", params.status);

  const response = await apiFetch(`/campaigns?${search.toString()}`);
  return response.json() as Promise<CampaignsListResult>;
}

export async function fetchCampaignDetail(campaignId: number): Promise<CampaignDetail> {
  const response = await apiFetch(`/campaigns/${campaignId}`);
  return response.json() as Promise<CampaignDetail>;
}

export async function startCampaign(campaignId: number): Promise<{ message: string }> {
  const response = await apiFetch(`/campaigns/${campaignId}/start`, { method: "POST" });
  return response.json() as Promise<{ message: string }>;
}

export async function stopCampaign(campaignId: number): Promise<{ message: string }> {
  const response = await apiFetch(`/campaigns/${campaignId}/stop`, { method: "POST" });
  return response.json() as Promise<{ message: string }>;
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
