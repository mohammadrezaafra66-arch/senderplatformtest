export interface DashboardSummary {
  campaigns_total: number;
  campaigns_running: number;
  campaigns_paused: number;
  messages_total: number;
  messages_sent: number;
  messages_failed: number;
  accounts_total: number;
  accounts_active: number;
  accounts_banned: number;
}

export interface QueueItem {
  name: string;
  pending: number;
}

export interface QueuesStatusResponse {
  queues: QueueItem[];
}

export interface WorkerItem {
  name: string;
  status: string;
  last_seen_at: string | null;
}

export interface WorkersStatusResponse {
  workers: WorkerItem[];
}

export interface DashboardSnapshot {
  type: "dashboard_snapshot";
  timestamp: string;
  summary: DashboardSummary;
  queues: QueueItem[];
  workers: WorkerItem[];
  warnings: string[];
  controls?: {
    kill_switch_enabled: boolean;
  };
}

export type WsConnectionState = "connecting" | "connected" | "disconnected" | "error";

export interface ControlsStatus {
  kill_switch: {
    enabled: boolean;
  };
  defaults: {
    delay_seconds: number;
  };
  redis: {
    available: boolean;
  };
}

export interface KillSwitchStatus {
  enabled: boolean;
  redis_key: string;
  updated_at: string | null;
}

export interface SetKillSwitchResponse {
  success: boolean;
  enabled: boolean;
  redis_key: string;
}

export interface AccountDelayStatus {
  account_id: number;
  delay_seconds: number;
  redis_key: string;
  source: string;
}

export interface SetAccountDelayResponse {
  success: boolean;
  account_id: number;
  delay_seconds: number;
  redis_key: string;
}
