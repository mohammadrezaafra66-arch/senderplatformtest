import type {
  AccountDelayStatus,
  ControlsStatus,
  DashboardSummary,
  KillSwitchStatus,
  QueuesStatusResponse,
  SetAccountDelayResponse,
  SetKillSwitchResponse,
  WorkersStatusResponse,
} from "./types";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ||
  "http://localhost:8001";

export const WS_URL =
  import.meta.env.VITE_WS_URL || "ws://localhost:8001/dashboard/ws";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: string | Array<{ msg?: string; loc?: string[] }>;
    };
    if (typeof body.detail === "string") {
      return body.detail;
    }
    if (Array.isArray(body.detail)) {
      return body.detail
        .map((item) => item.msg || JSON.stringify(item))
        .join("; ");
    }
  } catch {
    // ignore JSON parse errors
  }
  return `Request failed: ${response.status} ${response.statusText}`;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    const message = await parseErrorMessage(response);
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}

export function fetchDashboardSummary(): Promise<DashboardSummary> {
  return fetchJson<DashboardSummary>("/dashboard/summary");
}

export function fetchQueuesStatus(): Promise<QueuesStatusResponse> {
  return fetchJson<QueuesStatusResponse>("/dashboard/queues/status");
}

export function fetchWorkersStatus(): Promise<WorkersStatusResponse> {
  return fetchJson<WorkersStatusResponse>("/dashboard/workers/status");
}

export function getControlsStatus(): Promise<ControlsStatus> {
  return fetchJson<ControlsStatus>("/controls/status");
}

export function getKillSwitch(): Promise<KillSwitchStatus> {
  return fetchJson<KillSwitchStatus>("/controls/kill-switch");
}

export function setKillSwitch(enabled: boolean): Promise<SetKillSwitchResponse> {
  return fetchJson<SetKillSwitchResponse>("/controls/kill-switch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export function getAccountDelay(accountId: number): Promise<AccountDelayStatus> {
  return fetchJson<AccountDelayStatus>(`/controls/accounts/${accountId}/delay`);
}

export function setAccountDelay(
  accountId: number,
  delaySeconds: number,
): Promise<SetAccountDelayResponse> {
  return fetchJson<SetAccountDelayResponse>(
    `/controls/accounts/${accountId}/delay`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delay_seconds: delaySeconds }),
    },
  );
}
