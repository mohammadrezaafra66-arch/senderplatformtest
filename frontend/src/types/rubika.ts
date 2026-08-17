export type RubikaPoolPhase = "day" | "night" | "listener" | "status";

export type RubikaPoolAccountItem = {
  account_id: number;
  label: string | null;
  phone_number: string | null;
  account_status: string;
  phase: RubikaPoolPhase | "unassigned";
  priority: number;
  last_error_at: string | null;
  last_error_message: string | null;
  last_used_at: string | null;
};

export type RubikaAccountsListResult = {
  items: RubikaPoolAccountItem[];
  total_count: number;
};

export type RubikaPoolUpsertResult = {
  success: boolean;
  account_id: number;
  phase: RubikaPoolPhase;
  priority: number;
  message: string;
};

export type RubikaPoolRestoreResult = {
  success: boolean;
  account_id: number;
  account_status: string;
  message: string;
};

export type RubikaSendLogItem = {
  message_id: number;
  campaign_id: number;
  campaign_title: string | null;
  account_id: number;
  account_label: string | null;
  contact_id: number;
  contact_phone: string | null;
  rendered_text: string | null;
  status: string | null;
  platform_message_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
};

export type RubikaSendLogResult = {
  items: RubikaSendLogItem[];
  total_count: number;
  limit: number;
  offset: number;
};

export type RubikaGroupItem = {
  id: number;
  group_guid: string;
  group_name: string | null;
  listener_account_id: number | null;
  keywords: string[];
  keyword_response: string | null;
  red_keywords: string[];
  conversation_mode_enabled: boolean;
  is_active: boolean;
  created_at: string;
};

export type RubikaGroupsListResult = {
  items: RubikaGroupItem[];
  total_count: number;
};

export type RubikaGroupCreatePayload = {
  group_guid: string;
  group_name?: string | null;
  listener_account_id?: number | null;
  keywords?: string[];
  keyword_response?: string | null;
  red_keywords?: string[];
  conversation_mode_enabled?: boolean;
};

export type RubikaGroupUpdatePayload = Partial<{
  group_name: string | null;
  listener_account_id: number | null;
  keywords: string[];
  keyword_response: string | null;
  red_keywords: string[];
  conversation_mode_enabled: boolean;
  is_active: boolean;
}>;

export type RubikaGroupMessageItem = {
  id: number;
  sender_name: string | null;
  sender_phone: string | null;
  message_type: string;
  message_text: string | null;
  transcription: string | null;
  image_extracted_text: string | null;
  is_reply_to_our_message: boolean;
  has_red_keyword: boolean;
  received_at: string;
};

export type RubikaGroupMessagesResult = {
  group_id: number;
  items: RubikaGroupMessageItem[];
  total_count: number;
  limit: number;
  offset: number;
};

export type RubikaScheduleItem = {
  phase: string;
  start_hour: number;
  end_hour: number;
  max_per_hour: number;
  is_active: boolean;
};

export type RubikaScheduleListResult = {
  items: RubikaScheduleItem[];
};

export type RubikaUserLoginStartResult = {
  registration_token: string;
  stage: "code_required" | "pass_key_required";
  message: string;
  hint_pass_key?: string | null;
};

export type RubikaUserLoginVerifyResult = {
  success: boolean;
  account_id: number;
  guid: string;
  phone_number: string;
  message: string;
};

// ─── Phase 5 — Protection Center ───

export type RubikaHealthState =
  | "healthy"
  | "degraded"
  | "throttled"
  | "quarantined"
  | "critical"
  | "offline"
  | string;

export type RubikaCircuitState = "closed" | "open" | "half_open" | string;

export type RubikaRestoreEligibility = {
  allowed: boolean;
  code: string;
  reason: string;
};

export type RubikaPreflightDisplay = {
  send_allowed: boolean;
  code: string;
  label: string;
};

export type RubikaIncidentItem = {
  incident_id: string;
  scope: string;
  account_id: number | null;
  category: string;
  severity: string;
  status: string;
  opened_at: string;
  last_seen_at: string;
  resolved_at: string | null;
  reason: string;
  source: string;
  occurrence_count: number;
  code?: string | null;
};

export type RubikaAlertItem = {
  alert_id: string;
  type: string;
  severity: string;
  title: string;
  message: string;
  created_at: string;
  first_seen: string;
  last_seen: string;
  occurrence_count: number;
  dedupe_key: string;
  status: string;
  account_id: number | null;
  incident_id: string | null;
  metadata?: Record<string, unknown>;
};

export type RubikaProtectionAccountRow = {
  account_id: number;
  label: string | null;
  phone_number: string | null;
  delivery_mode: string | null;
  account_status: string;
  session_ready: boolean;
  session_code: string | null;
  session_message: string | null;
  readiness: { ready: boolean; code: string; message: string };
  preflight: RubikaPreflightDisplay;
  lifecycle_state: string;
  health_state: RubikaHealthState;
  pool_phase: string;
  priority: number;
  sent_today: number;
  daily_cap: number;
  remaining_daily: number;
  sent_this_hour: number;
  hourly_cap: number;
  remaining_hourly: number;
  minimum_interval_seconds: number;
  next_allowed_send_at: string | null;
  cooldown_until: string | null;
  last_successful_send_at: string | null;
  last_failure_at: string | null;
  last_failure_code: string | null;
  failure_rate: number;
  failures_window: number;
  successes_window: number;
  consecutive_failures: number;
  quarantined: boolean;
  quarantine_reason: string | null;
  last_incident: RubikaIncidentItem | null;
  restore: RubikaRestoreEligibility;
  health?: Record<string, unknown>;
  policy?: Record<string, unknown> | null;
};

export type RubikaProtectionOverview = {
  evaluated_at: string;
  redis_ok: boolean;
  system: {
    circuit: {
      state: RubikaCircuitState;
      opened_at: string | null;
      half_open_at: string | null;
      open_until: string | null;
      probe_budget: number;
      probe_remaining: number;
      systemic_account_count?: number;
      systemic_failure_count?: number;
      reason: string | null;
    };
    open_incidents: number;
    redis_ok: boolean;
  };
  summary: {
    total_accounts: number;
    ready: number;
    healthy: number;
    degraded: number;
    throttled: number;
    quarantined: number;
    requires_login: number;
    critical?: number;
    offline?: number;
    open_incidents: number;
    critical_incidents: number;
    circuit_state: RubikaCircuitState;
    unresolved_alerts: number;
    critical_alerts: number;
  };
  accounts: RubikaProtectionAccountRow[];
  incidents: RubikaIncidentItem[];
  alerts: RubikaAlertItem[];
  campaign_impact: {
    campaigns_affected_by_quarantine: number;
    pending_messages_blocked: number;
    retryable_blocked_messages: number;
    messages_waiting_circuit_open: number;
    running_campaigns?: number;
    campaigns_paused_by_circuit?: number;
    pending_rubika_messages?: number;
  };
  events: Array<{
    time: string | null;
    scope: string | null;
    account_id: number | null;
    event: string;
    reason: string | null;
    username?: string | null;
    result?: string | null;
    source: string;
  }>;
  operator_notes?: {
    circuit_force_close: boolean;
    circuit_force_close_reason: string;
  };
};

export type RubikaProtectionRestoreResult = {
  state: string;
  code: string;
  message: string;
  account_id: number;
  health_state?: string;
};
