export type CampaignStats = {
  total_recipients: number;
  queued: number;
  processing: number;
  sent: number;
  failed: number;
  progress_percent: number;
  eta_seconds: number | null;
};

export type CampaignSenderAccount = {
  account_id: number;
  label: string | null;
  account_identifier: string | null;
  platform: PlatformOption;
  status: string;
  priority: number;
  weight: number;
  enabled: boolean;
};

export type CampaignListItem = {
  id: number;
  name: string;
  title: string;
  platform: PlatformOption;
  status: string;
  created_at: string;
  total_recipients: number;
  account_ids?: number[];
  sender_accounts?: CampaignSenderAccount[];
};

export type CampaignDetail = {
  id: number;
  name: string;
  title: string;
  channel: string;
  platform: PlatformOption;
  status: string;
  template_text: string | null;
  use_gpt: boolean;
  include_products: boolean;
  created_at: string;
  updated_at: string;
  stats: CampaignStats;
  account_ids?: number[];
  sender_accounts?: CampaignSenderAccount[];
};

export type CampaignRecipientItem = {
  id: number;
  campaign_id: number;
  contact_id: number;
  phone: string | null;
  first_name: string | null;
  last_name: string | null;
  render_status: string;
  send_status: string;
  failure_reason: string | null;
  updated_at: string;
};

export type PlatformOption = "bale" | "telegram" | "whatsapp" | "rubika";

export type CreateCampaignFromImportPayload = {
  import_batch_id: number;
  title: string;
  platform: PlatformOption;
  template_text: string;
  use_gpt: boolean;
  include_products: boolean;
  account_ids: number[];
};

export type CampaignAccountsResult = {
  campaign_id: number;
  account_ids: number[];
  sender_accounts: CampaignSenderAccount[];
};
