export type DashboardSummary = {
  campaigns_total: number;
  campaigns_running: number;
  campaigns_paused: number;
  messages_total: number;
  messages_sent: number;
  messages_failed: number;
  accounts_total: number;
  accounts_active: number;
  accounts_banned: number;
};

export type QueueStatusItem = {
  name: string;
  pending: number;
};

export type WorkerStatusItem = {
  name: string;
  status: string;
  last_seen_at: string | null;
};
