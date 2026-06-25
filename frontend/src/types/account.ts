export type PlatformOption = "whatsapp" | "telegram" | "rubika" | "bale";

export type AccountStatusOption = "active" | "resting" | "banned" | "requires_login";

export type AccountItem = {
  id: number;
  platform: PlatformOption;
  account_identifier: string | null;
  label: string | null;
  status: AccountStatusOption;
  proxy_url: string | null;
  policy_id: number | null;
  created_at: string;
  updated_at: string;
  last_used_at: string | null;
};

export type AccountsListResult = {
  items: AccountItem[];
  total_count: number;
};

export type AccountCreatePayload = {
  platform: PlatformOption;
  account_identifier: string;
  label?: string | null;
  proxy_url?: string | null;
  status?: AccountStatusOption;
};

export type AccountUpdatePayload = {
  account_identifier?: string;
  label?: string | null;
  proxy_url?: string | null;
  status?: AccountStatusOption;
};

export type AccountTestConnectionResult = {
  success: boolean;
  account_id: number;
  platform: PlatformOption;
  message: string;
  error: string | null;
};

export type WhatsAppWebStatus = {
  account_id: number;
  delivery_mode: string;
  profile_dir: string;
  profile_exists: boolean;
  session_registered: boolean;
  linked: boolean;
  needs_qr: boolean;
  phone: string | null;
  linked_at: string | null;
  message: string;
};

export type WhatsAppWebRegisterResult = {
  success: boolean;
  account_id: number;
  message: string;
  profile_dir: string;
  linked: boolean;
};

export type WhatsAppWebLinkSession = {
  account_id: number;
  linked: boolean;
  status: string;
  qr_code_base64: string | null;
  message: string;
};

export type WhatsAppWebPoolWorkerItem = {
  hostname: string;
  pool_size: number;
  pool_index: number;
  assigned_account_ids: number[];
  updated_at: string | null;
};

export type WhatsAppWebPoolStatus = {
  workers: WhatsAppWebPoolWorkerItem[];
  total: number;
};

export type AccountSessionStatus = {
  account_id: number;
  platform: string;
  account_status: string;
  session_type: string;
  session_registered: boolean;
  ready_for_delivery: boolean;
  message: string;
  error: string | null;
  delivery_mode?: string | null;
  linked?: boolean | null;
  needs_qr?: boolean | null;
  profile_exists?: boolean | null;
  profile_dir?: string | null;
  linked_at?: string | null;
};

export type AccountSessionRegisterResult = {
  success: boolean;
  account_id: number;
  platform: PlatformOption;
  session_type: string;
  message: string;
};

export type AccountSendTestResult = {
  account_id: number;
  platform: string;
  dry_run: boolean;
  live_send: boolean;
  recipient: string;
  recipient_type: string;
  success: boolean;
  status: string;
  platform_message_id: string | null;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean;
  message: string;
};

export type OperationalSendCapabilities = {
  dry_run_default: boolean;
  ops_live_send_api_enabled: boolean;
  live_send_allowed: boolean;
  real_message_sending_enabled: boolean;
  channel_connectors_enabled: boolean;
  dry_run_env: boolean;
};

export type LiveSendPreflightCheck = {
  key: string;
  passed: boolean;
  message: string;
};

export type LiveSendPreflight = {
  account_id: number;
  platform: string;
  ready_for_live_send: boolean;
  checks: LiveSendPreflightCheck[];
};

export type EvolutionInstanceStatus = {
  account_id: number;
  instance_name: string | null;
  state: "open" | "close" | "connecting" | "disconnected";
  connected: boolean;
  phone: string | null;
  qr_code_base64: string | null;
  proxy_assigned: boolean;
  proxy_pool_id: string | null;
  connected_at: string | null;
};

export type EvolutionQrLinkSession = {
  account_id: number;
  instance_name: string;
  success: boolean;
  qr_code_base64: string | null;
  message: string;
  status: "qr_generated" | "already_connected" | "error";
};

export type ProxyAssignRequest = {
  host: string;
  port: number;
  protocol: "http" | "socks5";
  username?: string;
  password?: string;
  pool_id?: string;
};
