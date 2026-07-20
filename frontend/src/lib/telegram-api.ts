import { apiFetch } from "@/lib/api";
import type {
  TelegramLead,
  TelegramPoolAccount,
  TelegramSchedule,
} from "@/types/telegram";

const BASE = "/telegram-mtproto";

export async function fetchAccountPool(): Promise<TelegramPoolAccount[]> {
  const response = await apiFetch(`${BASE}/accounts/pool`);
  return response.json() as Promise<TelegramPoolAccount[]>;
}

export async function fetchSchedule(): Promise<TelegramSchedule> {
  const response = await apiFetch(`${BASE}/schedule`);
  return response.json() as Promise<TelegramSchedule>;
}

export async function updateSchedule(
  body: TelegramSchedule
): Promise<{ status: string }> {
  const response = await apiFetch(`${BASE}/schedule`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return response.json() as Promise<{ status: string }>;
}

export async function fetchLeads(limit = 100): Promise<TelegramLead[]> {
  const response = await apiFetch(`${BASE}/leads?limit=${limit}`);
  return response.json() as Promise<TelegramLead[]>;
}

// NOTE: session/start and session/verify are intentionally omitted.
// Backend is broken: start_phone_login is missing from
// core_engine/services/telegram_session_setup.py, and verify_phone_code
// is a keyword-only stub that does not accept two_step_password.
