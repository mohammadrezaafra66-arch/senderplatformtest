export type CampaignStats = {
  total_recipients: number;
  queued: number;
  processing: number;
  sent: number;
  failed: number;
  progress_percent: number;
  eta_seconds: number | null;
};

export type CampaignListItem = {
  id: number;
  name: string;
  title: string;
  platform: string;
  status: string;
  created_at: string;
  total_recipients: number;
};

export type CampaignDetail = {
  id: number;
  name: string;
  title: string;
  channel: string;
  platform: string;
  status: string;
  template_text: string | null;
  use_gpt: boolean;
  include_products: boolean;
  created_at: string;
  updated_at: string;
  stats: CampaignStats;
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
};
