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
