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
  latest_render_batch_id?: string | null;
  render_version?: string | null;
  committed_renders?: CommittedRenderSample[];
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
  final_message_id?: number | null;
  account_id?: number | null;
  sender_account?: MessageSenderAccount | null;
  updated_at: string;
  final_text_preview?: string | null;
  has_more?: boolean;
  has_long_text?: boolean;
  use_gpt?: boolean | null;
  include_products?: boolean | null;
  variation_id?: string | null;
  product_count?: number | null;
  render_batch_id?: string | null;
  render_version?: string | null;
  final_text_sha256?: string | null;
};

export type FrozenProductFact = {
  external_id: string | null;
  product_code: string | null;
  name: string | null;
  price: string | null;
  currency: string | null;
  display_price: string | null;
  source_updated_at: string | null;
  fetched_at: string | null;
};

export type MessageGptTrace = {
  use_gpt: boolean;
  provider?: string | null;
  model?: string | null;
  generation_batch_id?: string | null;
  variation_id?: string | null;
  generated_at?: string | null;
};

export type MessageProductTrace = {
  include_products: boolean;
  product_count: number;
  heading?: string | null;
  source?: string | null;
  provider?: string | null;
  fetched_at?: string | null;
  products?: FrozenProductFact[];
};

export type MessageLogDetail = CampaignRecipientItem & {
  rendered_message_id?: number | null;
  message_id?: number | null;
  attempt_no?: number | null;
  platform?: string | null;
  final_text?: string | null;
  gpt?: MessageGptTrace | null;
  products?: MessageProductTrace | null;
  error_code?: string | null;
  rendered_at?: string | null;
  sent_at?: string | null;
  created_at?: string | null;
};

export type CommittedRenderSample = {
  rendered_message_id: number;
  contact_id: number | null;
  recipient_name: string | null;
  sender_account_id: number | null;
  final_text: string;
  final_text_sha256: string | null;
  render_batch_id: string | null;
  render_version: string | null;
  use_gpt: boolean;
  variation_id: string | null;
  include_products: boolean;
  product_count: number;
  rendered_at: string | null;
  committed: boolean;
  label: string;
};

export type CampaignRenderPreviewSample = {
  committed: boolean;
  preview_kind: string;
  label: string;
  final_text: string;
  final_text_sha256: string;
  render_version: string;
  render_batch_id: string;
  template_source: string;
  use_gpt: boolean;
  generation_batch_id: string | null;
  variation_id: string | null;
  variation_label: string | null;
  include_products: boolean;
  product_count: number;
  product_heading: string | null;
  product_source: string | null;
  product_fetched_at: string | null;
  immutable_product_block: string;
  prose_text: string;
  unresolved_placeholders: string[];
  sample_warning: string;
};

export type CampaignRenderPreview = {
  ok: boolean;
  committed: boolean;
  preview_kind: string;
  label: string;
  render_version: string;
  render_batch_id: string;
  use_gpt: boolean;
  include_products: boolean;
  preview_count: number;
  preview_variables: Record<string, string>;
  preview_variables_required: string[];
  generation_batch_id: string | null;
  provider: string | null;
  model: string | null;
  product_error: { code?: string; message?: string } | null;
  samples: CampaignRenderPreviewSample[];
  message: string;
};

export type MessageSenderAccount = {
  account_id: number;
  label: string | null;
  account_identifier: string | null;
  platform: PlatformOption;
  status: string;
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
