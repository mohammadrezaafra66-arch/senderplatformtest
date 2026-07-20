import { apiFetch } from "@/lib/api";
import type {
  TelegramLead,
  TelegramPoolAccount,
  TelegramSchedule,
} from "@/types/telegram";

const BASE = "/telegram-mtproto";

export async function fetchAccountPool(): Promise<TelegramPoolAccount[]> {
  const res = await apiFetch(`${BASE}/accounts/pool`);
  return (await res.json()) as TelegramPoolAccount[];
}

export async function fetchSchedule(): Promise<TelegramSchedule> {
  const res = await apiFetch(`${BASE}/schedule`);
  return (await res.json()) as TelegramSchedule;
}

export async function updateSchedule(
  body: TelegramSchedule
): Promise<{ status: string }> {
  const res = await apiFetch(`${BASE}/schedule`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await res.json()) as { status: string };
}

export async function fetchLeads(limit = 100): Promise<TelegramLead[]> {
  const res = await apiFetch(`${BASE}/leads?limit=${limit}`);
  return (await res.json()) as TelegramLead[];
}

// NOTE: session/start and session/verify are intentionally omitted.
// Backend is broken: start_phone_login is missing from
// core_engine/services/telegram_session_setup.py, and verify_phone_code
// is a keyword-only stub that does not accept two_step_password.
