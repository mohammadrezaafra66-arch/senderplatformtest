import { useRouter } from "next/router";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { ApiError, fetchMe, requestToken, setUnauthorizedHandler } from "@/lib/api";
import { clearStoredToken, getStoredToken, setStoredToken } from "@/lib/auth-storage";

export type Role = "admin" | "operator" | "viewer";

type AuthState = {
  role: Role;
  username: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [role, setRole] = useState<Role>("viewer");
  const [username, setUsername] = useState<string | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  const logout = useCallback(() => {
    clearStoredToken();
    setIsAuthenticated(false);
    setUsername(null);
    setRole("viewer");
    void router.push("/login");
  }, [router]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setIsAuthenticated(false);
      setUsername(null);
      setRole("viewer");
      void router.push("/login");
    });
  }, [router]);

  useEffect(() => {
    let cancelled = false;

    async function restoreSession() {
      const token = getStoredToken();
      if (!token) {
        if (!cancelled) setIsLoading(false);
        return;
      }

      try {
        const me = await fetchMe(token);
        if (cancelled) return;
        setRole(me.role);
        setUsername(me.username);
        setIsAuthenticated(true);
      } catch {
        if (!cancelled) {
          clearStoredToken();
          setIsAuthenticated(false);
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    void restoreSession();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (user: string, password: string) => {
    const tokenResponse = await requestToken(user, password);
    setStoredToken(tokenResponse.access_token);
    const me = await fetchMe(tokenResponse.access_token);
    setRole(me.role);
    setUsername(me.username);
    setIsAuthenticated(true);
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      role,
      username,
      isAuthenticated,
      isLoading,
      login,
      logout,
    }),
    [role, username, isAuthenticated, isLoading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function getLoginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "نام کاربری یا رمز عبور اشتباه است.";
    if (error.status === 403) return "اجازه ورود ندارید.";
    return error.message;
  }
  return "خطا در ارتباط با سرور. دوباره تلاش کنید.";
}
