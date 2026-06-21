import { apiFetch } from "@/lib/api";

export type WhatsAppWarmupTriggerResponse = {
  success: boolean;
  message: string;
  pairedAccounts: number;
  totalJobs: number;
};

export async function triggerWhatsAppWarmup(): Promise<WhatsAppWarmupTriggerResponse> {
  const response = await apiFetch("/whatsapp/trigger-warmup", {
    method: "POST",
  });
  return response.json() as Promise<WhatsAppWarmupTriggerResponse>;
}
