import { apiFetch } from "@/lib/api";
import type { CampaignListItem } from "@/types/campaign";
import type { DashboardSummary, QueueStatusItem, WorkerStatusItem } from "@/types/dashboard";

export async function fetchDashboardSummary(): Promise<DashboardSummary> {
  const response = await apiFetch("/dashboard/summary");
  return response.json() as Promise<DashboardSummary>;
}

export async function fetchQueuesStatus(): Promise<QueueStatusItem[]> {
  const response = await apiFetch("/dashboard/queues/status");
  const data = (await response.json()) as { queues: QueueStatusItem[] };
  return data.queues;
}

export async function fetchWorkersStatus(): Promise<WorkerStatusItem[]> {
  const response = await apiFetch("/dashboard/workers/status");
  const data = (await response.json()) as { workers: WorkerStatusItem[] };
  return data.workers;
}

export async function fetchRecentCampaigns(limit = 5): Promise<CampaignListItem[]> {
  const response = await apiFetch(`/campaigns?limit=${limit}&offset=0`);
  const data = (await response.json()) as { items: CampaignListItem[] };
  return data.items;
}
