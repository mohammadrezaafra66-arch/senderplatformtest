import { clearStoredToken, getStoredToken } from "@/lib/auth-storage";
import type { Role } from "@/state/auth";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "/backend";

export class ApiError extends Error {
  status: number;
  code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type TokenResponse = {
  access_token: string;
  token_type: string;
};

export type MeResponse = {
  username: string;
  role: Role;
};

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler;
}

export async function requestToken(username: string, password: string): Promise<TokenResponse> {
  const body = new URLSearchParams();
  body.set("username", username);
  body.set("password", password);

  const response = await fetch(`${API_BASE_URL}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(response.status, detail.message, detail.code);
  }

  return response.json() as Promise<TokenResponse>;
}

export async function fetchMe(token?: string): Promise<MeResponse> {
  const response = await apiFetch("/auth/me", { token });
  return response.json() as Promise<MeResponse>;
}

async function readErrorDetail(response: Response): Promise<{ message: string; code: string | null }> {
  try {
    const data = (await response.json()) as {
      detail?: string | { msg?: string }[] | Record<string, unknown>;
    };
    if (typeof data.detail === "string") return { message: data.detail, code: null };
    if (Array.isArray(data.detail) && data.detail[0]?.msg) {
      return { message: data.detail[0].msg, code: null };
    }
    if (data.detail && typeof data.detail === "object" && !Array.isArray(data.detail)) {
      const record = data.detail as Record<string, unknown>;
      const code = typeof record.code === "string" ? record.code : null;
      if (typeof record.message === "string") return { message: record.message, code };
      if (typeof record.error === "string") return { message: record.error, code };
    }
  } catch {
    // ignore
  }
  return { message: response.statusText || "Request failed", code: null };
}

type ApiFetchOptions = RequestInit & {
  token?: string | null;
};

export async function apiFetch(path: string, options: ApiFetchOptions = {}): Promise<Response> {
  const { token = getStoredToken(), headers, ...rest } = options;
  const mergedHeaders = new Headers(headers);

  if (token) {
    mergedHeaders.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: mergedHeaders,
  });

  if (response.status === 401) {
    clearStoredToken();
    onUnauthorized?.();
    throw new ApiError(401, "Unauthorized");
  }

  if (response.status === 403) {
    throw new ApiError(403, "Insufficient permissions");
  }

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new ApiError(response.status, detail.message, detail.code);
  }

  return response;
}
